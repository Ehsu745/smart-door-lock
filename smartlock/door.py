"""DoorSystem：門鎖狀態機。

    face    ：等待 master 人臉連續通過 required_frames 幀
    gesture ：人臉通過後，gesture_window 秒內比出他綁定的手勢（gestures.json；沒綁定用 allowed_gestures 之一），
              連續 gesture_frames 幀 → 開鎖；比錯的幀數累計 gesture_wrong_frames → 失敗並冷卻（防止輪流試）
              綁定的手勢是第二道密碼：門口畫面、狀態、事件文字都不寫出是哪一個
    guest   ：只通知「偵測到訪客，建議開鎖」，不會自動開鎖

require_gesture=False 或沒有手勢偵測器時，master 人臉通過就直接開鎖。

process_frame() 一次處理一幀，時間由注入的 clock 提供，所以測試時可以用假相機、假時鐘逐幀驗證。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Callable, Optional

import cv2

from .camera import FrameReader
from .config import GESTURE_LABEL, Config
from .events import Event, EventBus, EventLog
from .face import face_crop
from .gesture import draw_hand

log = logging.getLogger(__name__)

NO_FACE = '無人臉'
MULTI_FACE = '多人入鏡，暫不自動解鎖'


class DoorSystem:
    def __init__(self, cfg: Config, arduino, recognizer, gesture=None,
                 bus: Optional[EventBus] = None, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.arduino = arduino
        self.recognizer = recognizer
        self.gesture = gesture
        self.bus = bus or EventBus()
        self.clock = clock
        self.event_log = EventLog()
        self.bus.subscribe('*', self._record)
        self.frame_hooks = []                # 每幀呼叫 hook(frame, faces, now)，例如哨兵錄影
        self.providers = {}                  # 名稱 → 回傳 dict 的函式，結果併入 snapshot()（網頁狀態）
        self.pins = None                     # PinStore；None = 只看 cfg.control_pin（測試用）
        self.bindings = None                 # GestureBindings；None = 所有人都用 cfg.allowed_gestures
        self.info = {}                       # 相機、手勢模式等靜態資訊，給網頁「設定」

        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.jpeg = None
        self.frame = None                    # 最新一幀（已畫框），給門口顯示視窗用
        self.unlocked_until = 0.0            # 推估的開鎖結束時間（Arduino 自動上鎖秒數）
        self.state = {'name': NO_FACE, 'category': '', 'distance': None,
                      'auto': True, 'message': '啟動中',
                      'require_gesture': cfg.require_gesture, 'phase': 'face', 'countdown': None,
                      'gesture': None, 'gesture_score': None, 'target': None, 'target_bound': False,
                      'visitor': None, 'visitor_since': None,
                      'allowed': cfg.gesture_labels()}

        # ---- 狀態機內部狀態 ----
        self._candidate = None               # 目前連續被認出的 master 編號
        self._streak = 0
        self._streak_min = None              # 這段連續通過中最小的距離（回報誤認時用來收緊門檻）
        self._cand_face = None               # (灰階畫面, 臉框)：最新一幀，人臉通過時裁切存檔
        self._attempt = None                 # 目前這次嘗試的編號（人臉通過 → 開鎖 / 逾時 / 離開）
        self._armed = True                   # False = 剛開過鎖，要等人離開才再允許
        self._absent_since = None
        self._last_name = None
        self._phase = 'face'
        self._target = None                  # 等手勢中的 master 編號
        self._target_name = ''
        self._deadline = 0.0
        self._last_seen = 0.0
        self._g_streak = 0
        self._g_last = None
        self._required = set(cfg.allowed_gestures)   # 這次驗證接受的手勢
        self._bound = False                  # 這次的主人有綁定手勢
        self._wrong = 0                      # 這次驗證比錯手勢的累計幀數
        self._cooldown_until = 0.0
        self._v_label = None                 # 訪客偵測
        self._v_streak = 0
        self._v_seen = 0.0
        self._v_notified = set()

    # ==========================================
    # 狀態、事件
    # ==========================================
    def update(self, **values):
        with self.guard:
            self.state.update(values)

    def emit(self, kind: str, text: str = '', **data):
        self.bus.publish(Event(kind, text, data))

    def _record(self, event: Event):
        if event.text:
            log.info(event.text)
        self.event_log(event)

    def snapshot(self) -> dict:
        with self.guard:
            data = dict(self.state)
        data['events'] = self.event_log.recent()
        data['arduino'] = self.arduino.status
        data['connected'] = self.arduino.connected
        data['pin_required'] = self.pins.required if self.pins is not None else bool(self.cfg.control_pin)
        left = self.unlocked_until - self.clock()
        data['unlocked'] = left > 0
        data['unlock_left'] = round(max(0.0, left), 1)
        data['unlock_seconds'] = self.cfg.unlock_seconds
        for name, provider in self.providers.items():
            try:
                data[name] = provider()
            except Exception:
                log.exception('狀態提供者 %s 失敗', name)
        return data

    def door(self, command: str, reason: str, **extra):
        """送開鎖／上鎖指令給 Arduino，並發出對應事件。回傳 (ok, message)。extra 併入事件資料（例如 attempt）。"""
        ok, msg = self.arduino.send(command)
        if ok:
            self.unlocked_until = self.clock() + self.cfg.unlock_seconds if command == 'UNLOCK' else 0.0
            if command == 'UNLOCK':
                self.update(visitor=None, visitor_since=None)
        self.update(message=msg)
        done = '已開鎖' if command == 'UNLOCK' else '已上鎖'
        text = f'{reason} → {done}' if ok else f'{reason} → {"開鎖" if command == "UNLOCK" else "上鎖"}失敗：{msg}'
        if ok:
            self.emit(command.lower(), text, reason=reason, **extra)
        else:
            self.emit('door_failed', text, command=command, message=msg, **extra)
        return ok, msg

    # ==========================================
    # 逐幀處理
    # ==========================================
    def process_frame(self, frame):
        """處理一幀：辨識、畫框、推進狀態機。frame 會被就地畫上標示並回傳。"""
        cfg = self.cfg
        now = self.clock()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.recognizer.identify(gray)

        accepted, name, category, best, guest_label = None, NO_FACE, '', None, None
        for f in faces:
            x, y, w, h = f.box
            color = ((0, 255, 0) if f.kind == 'master' else (200, 0, 200)) if f.known else (0, 0, 255)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            tag = (f.person if f.kind == 'master' else f'{f.person} (guest)') if f.known else 'Unknown'
            cv2.putText(frame, f'{tag} {f.distance:.0f}', (x, max(20, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        if len(faces) == 1:
            f = faces[0]
            name, category, best = (f.person, f.kind, f.distance) if f.known else ('Unknown', 'Stranger', f.distance)
            accepted = f.label if f.is_master else None          # 只有 master 能自動開鎖
            guest_label = f.label if f.is_guest else None
        elif len(faces) > 1:
            name = MULTI_FACE
        if name != self._last_name and name != NO_FACE:
            self.emit('seen', f'看到 {name} {category}'.strip(), name=name, category=category)
        self._last_name = name

        self._track_visitor(guest_label, name, now)
        self._track_rearm(len(faces), now)
        if accepted is not None:
            same = self._candidate == accepted
            self._streak = self._streak + 1 if same else 1
            self._streak_min = min(self._streak_min, best) if same and self._streak_min is not None else best
            self._candidate, self._cand_face = accepted, (gray, faces[0].box)
        else:
            self._candidate, self._streak, self._streak_min, self._cand_face = None, 0, None, None

        g_name, g_score = self._advance(frame, accepted, name, now)
        for hook in self.frame_hooks:
            try:
                hook(frame, faces, now)
            except Exception:
                log.exception('frame hook 失敗')

        # 綁定手勢時狀態 API 不給目前看到的手勢：開鎖那一刻看到的就是答案（/api/status 不需要登入）。
        # 用 _bound 而不是 phase 判斷：開鎖那一幀已經切回 face 階段，但 g_name 還是剛比出的綁定手勢
        hide = self._bound or g_name is None
        self.update(name=name, category=category,
                    distance=None if best is None else round(float(best), 1),
                    phase=self._phase, gesture=None if hide else g_name,
                    target=self._target_name if self._phase == 'gesture' else None,
                    target_bound=self._bound if self._phase == 'gesture' else False,
                    gesture_score=None if hide or g_score is None else round(float(g_score), 2),
                    countdown=round(max(0.0, self._deadline - now), 1) if self._phase == 'gesture' else None)
        return frame

    def _track_visitor(self, guest_label, name, now):
        """訪客：連續認出 required_frames 幀就通知一次；離開 rearm_seconds 秒後可再通知。"""
        if guest_label is not None:
            self._v_streak = self._v_streak + 1 if guest_label == self._v_label else 1
            self._v_label, self._v_seen = guest_label, now
            if self._v_streak >= self.cfg.required_frames and guest_label not in self._v_notified:
                self._v_notified.add(guest_label)
                self.update(visitor=name, visitor_since=time.strftime('%H:%M:%S'))
                self.emit('guest_detected', f'偵測到訪客：{name}，建議開鎖（請在網頁按「解鎖」）', name=name)
        else:
            self._v_streak = 0
            if self._v_notified and now - self._v_seen >= self.cfg.rearm_seconds:
                self._v_notified.clear()
        with self.guard:
            visitor = self.state['visitor']
        if visitor and now - self._v_seen >= self.cfg.visitor_hold:
            self.update(visitor=None, visitor_since=None)

    def _track_rearm(self, n_faces, now):
        """鏡頭裡沒有臉 rearm_seconds 秒後，才重新允許自動開鎖。"""
        if n_faces == 0:
            self._absent_since = self._absent_since or now
            if now - self._absent_since >= self.cfg.rearm_seconds:
                self._armed = True
        else:
            self._absent_since = None

    def _new_attempt(self, name) -> dict:
        """人臉通過：開一筆嘗試。_crop 是給 AttemptLog 存檔的人臉圖，底線開頭的欄位不會寫進資料庫。"""
        crop = face_crop(*self._cand_face) if self._cand_face else None
        return {'attempt': uuid.uuid4().hex[:12], 'name': name,
                'distance': None if self._streak_min is None else round(float(self._streak_min), 1), '_crop': crop}

    def _advance(self, frame, accepted, name, now):
        """推進 face / gesture 階段。回傳這一幀的 (手勢名稱, 信心值)。"""
        cfg = self.cfg
        with self.guard:
            auto = self.state['auto']
        g_name, g_score = None, None
        if not auto:
            self._phase = 'face'
        elif self._phase == 'face':
            if self._armed and self._streak >= cfg.required_frames and now >= self._cooldown_until:
                att = self._new_attempt(name)
                if self.gesture is None:
                    self._armed = False
                    self.door('UNLOCK', f'辨識成功 {name}', **att)
                else:
                    self._phase, self._target, self._target_name = 'gesture', self._candidate, name
                    self._deadline, self._last_seen, self._g_streak, self._g_last = now + cfg.gesture_window, now, 0, None
                    self._attempt = att['attempt']
                    bound = self.bindings.get(name) if self.bindings is not None else None
                    self._required = {bound} if bound else set(cfg.allowed_gestures)
                    self._bound, self._wrong = bool(bound), 0
                    hint = '請比出綁定手勢' if bound else '請比出手勢：' + ' 或 '.join(cfg.gesture_labels())
                    self.emit('face_passed', f'人臉通過 {name} → {hint}', **att)
        elif self._phase == 'gesture':
            if accepted == self._target:
                self._last_seen = now
            g_name, g_score, pts = self.gesture.detect(frame)
            confident = bool(g_name) and g_name != 'None' and g_score >= cfg.gesture_score
            ok_g = confident and g_name in self._required
            if pts:
                draw_hand(frame, pts)
            self._g_streak = self._g_streak + 1 if ok_g and g_name == self._g_last else (1 if ok_g else 0)
            self._g_last = g_name if ok_g else None
            if confident and not ok_g:
                self._wrong += 1                  # 累計，不是連續：輪流試每個只比幾幀也會被抓到
            left = self._deadline - now
            # 綁定手勢時不顯示該比哪個，也不顯示目前看到的手勢（畫面會串流到手機）
            want = 'your gesture' if self._bound else ' / '.join(cfg.allowed_gestures)
            cv2.putText(frame, f'Show {want}  {max(0, left):.0f}s', (16, 36), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0, 220, 255), 2)
            if g_name and not self._bound:
                cv2.putText(frame, f'{g_name} {g_score:.2f}', (16, 66), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, (0, 200, 255), 2)
            if self._wrong >= cfg.gesture_wrong_frames:
                self._phase, self._cooldown_until = 'face', now + cfg.retry_cooldown
                self.emit('gesture_wrong', f'{self._target_name} 手勢不正確，{cfg.retry_cooldown:.0f} 秒後可重試',
                          name=self._target_name, attempt=self._attempt)
            elif self._g_streak >= cfg.gesture_frames:
                self._armed, self._phase = False, 'face'
                how = '綁定手勢' if self._bound else f'手勢 {GESTURE_LABEL.get(g_name, g_name)}'
                self.door('UNLOCK', f'人臉 {self._target_name} + {how}',
                          attempt=self._attempt, name=self._target_name)
            elif now - self._last_seen > cfg.face_grace:
                self._phase = 'face'
                self.emit('gesture_cancel', f'{self._target_name} 離開鏡頭，取消手勢驗證', name=self._target_name,
                          attempt=self._attempt)
            elif left <= 0:
                self._phase, self._cooldown_until = 'face', now + cfg.retry_cooldown
                self.emit('gesture_timeout', f'{self._target_name} 手勢逾時（{cfg.gesture_window:.0f} 秒），'
                          f'{cfg.retry_cooldown:.0f} 秒後可重試', name=self._target_name, attempt=self._attempt)
        return g_name, g_score

    # ==========================================
    # 背景迴圈
    # ==========================================
    def run_loop(self, camera):
        """背景執行緒：持續讀相機 → process_frame → 存最新畫面給網頁和視窗。"""
        reader = FrameReader(camera)
        try:
            while not self.stop.is_set():
                frame = reader.read()
                if frame is None:
                    continue
                self.process_frame(frame)
                ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with self.guard:
                        self.jpeg = buf.tobytes()
                        self.frame = frame
        except Exception as exc:
            self.update(message=f'辨識停止：{exc}')
            log.error('辨識錯誤：%s', exc)
