"""手勢辨識（MediaPipe）：優先用官方 Gesture Recognizer，沒有模型時改用 Hands + 關鍵點規則。"""
from __future__ import annotations

import logging
import math
import shutil
import sys
import time

import cv2

from .config import GESTURE_MODEL_URL, Config

log = logging.getLogger(__name__)

HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
                    (17, 18), (18, 19), (19, 20)]


def classify_landmarks(pts):
    """以 MediaPipe Hands 的 21 個關鍵點（正規化座標，y 向下）判斷手勢。
    回傳與 Gesture Recognizer 相同的類別名稱，無法判斷時回傳 None。"""
    def d(a, b):
        return math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
    size = d(0, 9) or 1e-6                                   # 手腕到中指根部，當作手掌大小
    extended = {f: d(tip, 0) > d(pip, 0) * 1.15              # 指尖比第二關節離手腕更遠 = 伸直
                for f, (pip, tip) in {'index': (6, 8), 'middle': (10, 12), 'ring': (14, 16),
                                      'pinky': (18, 20)}.items()}
    thumb_out = d(4, 5) > size * 0.55 and d(4, 17) > d(3, 17)   # 拇指尖離食指根夠遠
    n_fingers = sum(extended.values())
    thumb_dy = (pts[2][1] - pts[4][1]) / size                # >0 表示拇指尖在拇指根上方
    if n_fingers == 0 and thumb_out and thumb_dy > 0.6:
        return 'Thumb_Up'
    if n_fingers == 0 and thumb_out and thumb_dy < -0.6:
        return 'Thumb_Down'
    if extended['index'] and extended['middle'] and not extended['ring'] and not extended['pinky']:
        return 'Victory'
    if extended['index'] and not extended['middle'] and not extended['ring'] and extended['pinky']:
        return 'ILoveYou' if thumb_out else None
    if extended['index'] and n_fingers == 1:
        return 'Pointing_Up'
    if n_fingers == 4:
        return 'Open_Palm'
    if n_fingers == 0 and not thumb_out:
        return 'Closed_Fist'
    return None


def ensure_gesture_model(cfg: Config) -> bool:
    """沒有模型檔就自動下載（約 8 MB）；失敗回傳 False，改用備援模式。"""
    path = cfg.gesture_model
    if path.exists() and path.stat().st_size > 100_000:
        return True
    tmp = path.with_suffix('.part')
    try:
        import ssl
        import urllib.request
        try:                                   # macOS 的 python.org 版 Python 常缺根憑證，改用 certifi
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        path.parent.mkdir(exist_ok=True)
        log.info('下載 MediaPipe 手勢模型...')
        with urllib.request.urlopen(GESTURE_MODEL_URL, context=ctx, timeout=30) as resp, open(tmp, 'wb') as out:
            shutil.copyfileobj(resp, out)
        tmp.rename(path)
        log.info('手勢模型已存到 %s', path)
        return True
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        log.info('手勢模型下載失敗（%s），改用備援模式：MediaPipe Hands + 關鍵點規則', exc)
        log.info('  可以手動下載：curl -L -o %s %s', path, GESTURE_MODEL_URL)
        return False


class GestureDetector:
    """detect(frame) → (手勢名稱或 None, 信心值, 21 個關鍵點的像素座標或 None)"""

    def __init__(self, cfg: Config):
        try:
            import mediapipe as mp
        except ImportError:
            v = f'{sys.version_info.major}.{sys.version_info.minor}'
            raise RuntimeError(f'目前的 Python {v} 沒有 mediapipe：請在 Python 3.9–3.12 的 venv 執行 '
                               'pip install -r requirements.txt（見 SETUP.md），並確認有 source .venv/bin/activate')
        self.mp = mp
        self.recognizer = None
        self.hands = None
        self.last_ts = -1
        self.t0 = time.monotonic()
        self.mode = ''
        if ensure_gesture_model(cfg):
            try:
                from mediapipe.tasks.python import BaseOptions, vision
                options = vision.GestureRecognizerOptions(
                    base_options=BaseOptions(model_asset_path=str(cfg.gesture_model)),
                    running_mode=vision.RunningMode.VIDEO, num_hands=1,
                    min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
                    min_tracking_confidence=0.5)
                self.recognizer = vision.GestureRecognizer.create_from_options(options)
                self.mode = 'MediaPipe Gesture Recognizer'
            except Exception as exc:
                log.info('手勢模型載入失敗（%s）：刪除 %s 後重新執行可重新下載；先改用備援模式', exc, cfg.gesture_model)
        if self.recognizer is None:
            self.hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=1,
                                                  model_complexity=0, min_detection_confidence=0.6,
                                                  min_tracking_confidence=0.5)
            self.mode = 'MediaPipe Hands + 規則'
        log.info('手勢辨識：%s', self.mode)

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        if self.recognizer is not None:
            ts = max(int((time.monotonic() - self.t0) * 1000), self.last_ts + 1)   # VIDEO 模式時間戳必須遞增
            self.last_ts = ts
            image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            result = self.recognizer.recognize_for_video(image, ts)
            if not result.hand_landmarks:
                return None, 0.0, None
            pts = [(int(p.x * w), int(p.y * h)) for p in result.hand_landmarks[0]]
            if result.gestures and result.gestures[0]:
                top = result.gestures[0][0]
                name = None if top.category_name in ('None', '') else top.category_name
                return name, float(top.score), pts
            return None, 0.0, pts
        result = self.hands.process(rgb)
        if not result.multi_hand_landmarks:
            return None, 0.0, None
        lm = result.multi_hand_landmarks[0].landmark
        name = classify_landmarks([(p.x, p.y) for p in lm])
        return name, (0.9 if name else 0.0), [(int(p.x * w), int(p.y * h)) for p in lm]

    def close(self):
        if self.recognizer is not None:
            self.recognizer.close()
        if self.hands is not None:
            self.hands.close()


def draw_hand(frame, pts, ok):
    color = (0, 220, 0) if ok else (0, 200, 255)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], color, 2)
    for p in pts:
        cv2.circle(frame, p, 3, (255, 255, 255), -1)
