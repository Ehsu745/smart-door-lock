"""哨兵：陌生人在門口逗留就自動錄影。

狀態
  idle      ：沒有陌生人（仍持續把畫面放進預錄緩衝區）
  watching  ：看到陌生人，計時中；期間只要認出 master 就取消
  recording ：陌生人連續出現 ≥ sentinel_trigger 秒 → 寫入影片（含觸發前 sentinel_preroll 秒）
              陌生人離開 ≥ sentinel_stop 秒就停止；單段超過 sentinel_max 秒就切成下一段

畫面以固定幀率（sentinel_fps）取樣：辨識迴圈比較慢時重複上一幀，影片播放速度才會和實際時間一致。
寫檔在背景執行緒進行，不拖慢辨識。

發出的事件
  stranger_detected  開始錄影（data: record_id, notify — 是否超過通知冷卻時間，給 LINE 通知使用）
  sentinel_saved     一段錄影完成（data: record_id, duration）
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable, Optional

import cv2

from .config import Config
from .events import Event, EventBus
from .storage import Storage

log = logging.getLogger(__name__)

FOURCC = 'MJPG'              # OpenCV 內建編碼器，不需要 ffmpeg；Pi 上 CPU 負擔低
EXT = '.avi'
MAX_DUP = 30                 # 辨識迴圈卡住時，最多補幾幀（避免卡很久後一次寫入大量重複畫面）


class Sentinel:
    def __init__(self, cfg: Config, storage: Storage, bus: EventBus,
                 wallclock: Optional[Callable[[], float]] = None, background: bool = True):
        self.cfg = cfg
        self.storage = storage
        self.bus = bus
        self.wallclock = wallclock or storage.wallclock       # 檔名與資料庫時間，和容量管理用同一個時鐘
        self.interval = 1.0 / cfg.sentinel_fps
        self.buffer = deque(maxlen=max(1, int(cfg.sentinel_preroll * cfg.sentinel_fps)))

        self.state = 'idle'
        self._next_slot = None              # 下一個取樣時間點
        self._watch_start = None
        self._last_stranger = 0.0
        self._last_notify = None
        self._retry_at = 0.0                # SD 卡空間不足時，下次再嘗試錄影的時間
        self._rec = None                    # 錄影中的資訊（dict）
        self._lock = threading.Lock()

        self._writers = {}                  # record_id → cv2.VideoWriter（只在寫檔執行緒使用）
        self._jobs: queue.Queue = queue.Queue()
        self._worker = None
        if background:
            self._worker = threading.Thread(target=self._work, daemon=True, name='sentinel-writer')
            self._worker.start()

    # ==========================================
    # 每幀呼叫（在辨識執行緒）
    # ==========================================
    def observe(self, frame, faces, now: float) -> None:
        cfg = self.cfg
        strangers = [f for f in faces if not f.known and f.box[2] >= cfg.sentinel_min_face]
        master = any(f.is_master for f in faces)
        slots = self._slots(now)

        if self.state == 'recording':
            if strangers:
                self._last_stranger = now
                self._best(frame, strangers)
            self._write(frame, slots)
            if now - self._last_stranger >= cfg.sentinel_stop:
                self._finish(now)
                self.state = 'idle'
            elif now - self._rec['start'] >= cfg.sentinel_max:
                self._finish(now)                               # 還在門口：直接接下一段
                self._start(now, frame, strangers, continued=True)
            return

        self._buffer(frame, slots)
        if master:
            self.state, self._watch_start = 'idle', None
        elif strangers:
            if self._watch_start is None:
                self._watch_start = now
            self._last_stranger = now
            self.state = 'watching'
            if now - self._watch_start >= cfg.sentinel_trigger and now >= self._retry_at:
                self._start(now, frame, strangers)
        elif self._watch_start is not None and now - self._last_stranger > cfg.face_grace:
            self.state, self._watch_start = 'idle', None

    def status(self, now: Optional[float] = None) -> dict:
        data = {'enabled': True, 'state': self.state}
        if self.state == 'recording' and now is not None:
            data['seconds'] = round(now - self._rec['start'])
        elif self.state == 'watching' and now is not None:
            data['seconds'] = round(now - self._watch_start, 1)
        return data

    # ==========================================
    # 取樣與緩衝
    # ==========================================
    def _slots(self, now: float) -> int:
        """這一幀要佔幾個固定幀率的時間格（0 = 太快，略過；>1 = 迴圈太慢，重複寫入）。"""
        if self._next_slot is None or now - self._next_slot > MAX_DUP * self.interval:
            self._next_slot = now
        n = 0
        while now >= self._next_slot and n < MAX_DUP:
            self._next_slot += self.interval
            n += 1
        return n

    def _buffer(self, frame, slots):
        if slots:
            copy = frame.copy()
            for _ in range(slots):
                self.buffer.append(copy)

    def _best(self, frame, strangers):
        """截圖：保留臉最大（最清楚）的一幀；同時記錄最小 LBPH 距離。"""
        rec = self._rec
        area = max(f.box[2] * f.box[3] for f in strangers)
        dist = min(f.distance for f in strangers)
        rec['min_distance'] = dist if rec['min_distance'] is None else min(rec['min_distance'], dist)
        if area > rec['best_area']:
            rec['best_area'], rec['best'] = area, frame.copy()

    # ==========================================
    # 開始 / 寫入 / 結束
    # ==========================================
    def _start(self, now, frame, strangers, continued=False):
        cfg = self.cfg
        if not self.storage.disk_ok():
            self.storage.enforce()
            if not self.storage.disk_ok():
                log.warning('SD 卡剩餘空間不足 %.0f MB，這次不錄影（60 秒後再試）', cfg.record_min_free_mb)
                self._retry_at = now + 60
                if continued:
                    self.state = 'idle'
                return
        notify = not continued and (self._last_notify is None or now - self._last_notify >= cfg.sentinel_cooldown)
        if notify:
            self._last_notify = now
        wall = self.wallclock()
        name = datetime.fromtimestamp(wall).strftime('%Y%m%d-%H%M%S') + EXT
        rid = self.storage.start_record(wall - (0 if continued else len(self.buffer) * self.interval),
                                        f'clips/{name}', notify)
        h, w = frame.shape[:2]
        self._rec = {'id': rid, 'start': now, 'video': f'clips/{name}', 'size': (w, h),
                     'best': None, 'best_area': 0, 'min_distance': None,
                     'preroll': 0 if continued else len(self.buffer)}
        self._submit(('open', rid, self.storage.path(f'clips/{name}'), (w, h)))
        if not continued:
            for f in self.buffer:
                self._submit(('frame', rid, f))
        self.buffer.clear()
        self._best(frame, strangers)
        self.state = 'recording'
        if not continued:
            self.bus.publish(Event('stranger_detected',
                                   f'哨兵：陌生人在門口超過 {cfg.sentinel_trigger:.0f} 秒，開始錄影（#{rid}）',
                                   {'record_id': rid, 'notify': notify}))

    def _write(self, frame, slots):
        if slots:
            copy = frame.copy()
            for _ in range(slots):
                self._submit(('frame', self._rec['id'], copy))

    def _finish(self, now):
        rec, self._rec = self._rec, None
        duration = now - rec['start'] + rec['preroll'] * self.interval
        self._submit(('close', rec, duration))
        self._watch_start = None

    def close(self, now: Optional[float] = None):
        """程式結束時呼叫：把錄到一半的影片收尾。"""
        if self._rec is not None:
            self._finish(now if now is not None else self._rec['start'])
            self.state = 'idle'
        if self._worker is not None:
            self._jobs.put(None)
            self._worker.join(timeout=10)

    # ==========================================
    # 背景寫檔
    # ==========================================
    def _submit(self, job):
        if self._worker is None:
            self._handle(job)
        else:
            self._jobs.put(job)

    def _work(self):
        while True:
            try:
                job = self._jobs.get(timeout=3600)
            except queue.Empty:
                self._safe(self.storage.enforce)           # 每小時檢查一次保留天數
                continue
            if job is None:
                return
            self._safe(self._handle, job)

    @staticmethod
    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception:
            log.exception('哨兵寫檔失敗')

    def _handle(self, job):
        kind = job[0]
        if kind == 'open':
            _, rid, path, size = job
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*FOURCC), self.cfg.sentinel_fps, size)
            if not writer.isOpened():
                raise RuntimeError(f'無法建立影片檔 {path}')
            self._writers[rid] = writer
        elif kind == 'frame':
            _, rid, frame = job
            writer = self._writers.get(rid)
            if writer is not None:
                writer.write(frame)
        elif kind == 'close':
            _, rec, duration = job
            writer = self._writers.pop(rec['id'], None)
            ok = writer is not None
            if writer is not None:
                writer.release()
            snap = None
            if rec['best'] is not None:
                snap = rec['video'].replace(EXT, '.jpg')
                cv2.imwrite(str(self.storage.path(snap)), rec['best'], [cv2.IMWRITE_JPEG_QUALITY, 85])
            self.storage.finish_record(rec['id'], duration, snap, rec['min_distance'], ok)
            self.storage.enforce()
            self.bus.publish(Event('sentinel_saved', f'哨兵：錄影 #{rec["id"]} 已儲存（{duration:.0f} 秒）',
                                   {'record_id': rec['id'], 'duration': round(duration, 1)}))
