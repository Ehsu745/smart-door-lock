"""所有設定集中在這裡。其他模組只讀 Config 物件，不直接讀環境變數。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# 專案根目錄：資料（faces/、face.yml、id_map.txt、models/）都放在這裡
PROJECT_ROOT = Path(__file__).resolve().parent.parent

GESTURE_MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/gesture_recognizer/'
                     'gesture_recognizer/float16/latest/gesture_recognizer.task')

# 純文字標籤（Pi 的終端機 / 瀏覽器預設沒有 emoji 字型）
GESTURE_LABEL = {'Thumb_Up': '讚(Thumb_Up)', 'Thumb_Down': '倒讚(Thumb_Down)', 'Victory': 'YA(Victory)',
                 'Open_Palm': '張開手(Open_Palm)', 'Closed_Fist': '握拳(Closed_Fist)',
                 'Pointing_Up': '食指朝上(Pointing_Up)', 'ILoveYou': 'ILoveYou'}

_EOL = {'lf': '\n', 'crlf': '\r\n', 'cr': '\r', 'none': ''}


def _flag(env, name, default):
    return env.get(name, default) == '1'


@dataclass
class Config:
    base: Path = PROJECT_ROOT

    # ---- 人臉 ----
    frame_size: Tuple[int, int] = (640, 480)
    face_size: Tuple[int, int] = (200, 200)       # 訓練與辨識前統一尺寸
    min_face: Tuple[int, int] = (80, 80)
    capture_count: int = 30
    threshold: float = 70.0                       # LBPH 距離，越低越像
    required_frames: int = 8                      # 同一人連續通過幾幀才算通過
    rearm_seconds: float = 3.0                    # 人離開鏡頭幾秒後才允許再次自動開鎖

    # ---- Arduino ----
    arduino_port: Optional[str] = None            # None = 自動尋找
    baud: int = 9600                              # 需與 Arduino 的 Serial.begin 一致
    cmd_unlock: str = 'unlock'                    # 與 Serial Monitor 手動輸入相同
    cmd_lock: str = 'lock'
    cmd_eol: str = '\n'
    ack_strict: bool = True                       # True = 必須收到 Arduino 回覆才算成功
    unlock_seconds: float = 8.0                   # 與 door_lock.ino 的 UNLOCK_DURATION 一致（顯示與模擬用）

    # ---- 相機 ----
    camera: str = 'auto'                          # auto | rpicam | picamera2 | usb
    camera_index: int = 0

    # ---- 手勢 ----
    require_gesture: bool = True                  # False = 只用人臉開鎖
    allowed_gestures: Tuple[str, ...] = ('Thumb_Up', 'Victory')
    gesture_score: float = 0.6
    gesture_frames: int = 5                       # 連續幾幀同一個合格手勢才算
    gesture_window: float = 8.0                   # 人臉通過後幾秒內要比出手勢
    face_grace: float = 3.0                       # 比手勢時臉被手擋住，超過這麼久才取消
    retry_cooldown: float = 3.0                   # 手勢逾時後隔幾秒才重新開始
    visitor_hold: float = 10.0                    # 訪客離開後，網頁提示保留幾秒

    # ---- 網頁 / 顯示 ----
    web_port: int = 5050
    enable_qr: bool = True
    show_window: str = 'auto'                     # auto | 1 | 0
    kiosk: bool = False
    control_pin: str = ''
    public_url: str = ''
    simulate: bool = False                        # True = 不接 Arduino，用文字模擬

    # ---- 路徑 ----
    @property
    def face_dir(self) -> Path:
        return self.base / 'faces'

    @property
    def model_file(self) -> Path:
        return self.base / 'face.yml'

    @property
    def map_file(self) -> Path:
        return self.base / 'id_map.txt'

    @property
    def gesture_model(self) -> Path:
        return self.base / 'models' / 'gesture_recognizer.task'

    def cmd_text(self, command: str) -> str:
        return {'UNLOCK': self.cmd_unlock, 'LOCK': self.cmd_lock}[command]

    def gesture_labels(self):
        return [GESTURE_LABEL.get(g, g) for g in self.allowed_gestures]

    @classmethod
    def from_env(cls, env=None, base: Optional[Path] = None) -> 'Config':
        """從環境變數建立設定；沒有設定的項目用預設值。"""
        env = os.environ if env is None else env
        gestures = tuple(g.strip() for g in env.get('ALLOWED_GESTURES', 'Thumb_Up,Victory').split(',') if g.strip())
        eol = env.get('CMD_EOL', 'lf')
        if eol not in _EOL:
            raise ValueError(f'CMD_EOL 只能是 {"/".join(_EOL)}，目前是 {eol!r}')
        return cls(
            base=Path(base) if base else PROJECT_ROOT,
            threshold=float(env.get('FACE_THRESHOLD', 70)),
            arduino_port=env.get('ARDUINO_PORT') or None,
            baud=int(env.get('ARDUINO_BAUD', 9600)),
            cmd_unlock=env.get('CMD_UNLOCK', 'unlock'),
            cmd_lock=env.get('CMD_LOCK', 'lock'),
            cmd_eol=_EOL[eol],
            ack_strict=_flag(env, 'ARDUINO_STRICT', '1'),
            camera=env.get('CAMERA', 'auto'),
            camera_index=int(env.get('CAMERA_INDEX', 0)),
            require_gesture=_flag(env, 'REQUIRE_GESTURE', '1'),
            allowed_gestures=gestures,
            gesture_score=float(env.get('GESTURE_SCORE', 0.6)),
            gesture_frames=int(env.get('GESTURE_FRAMES', 5)),
            gesture_window=float(env.get('GESTURE_WINDOW', 8)),
            web_port=int(env.get('WEB_PORT', 5050)),
            enable_qr=_flag(env, 'ENABLE_QR', '1'),
            show_window=env.get('SHOW_WINDOW', 'auto'),
            kiosk=_flag(env, 'KIOSK', '0'),
            control_pin=env.get('CONTROL_PIN', ''),
            public_url=env.get('PUBLIC_URL', ''),
            simulate=_flag(env, 'SIM_ARDUINO', '0'),
        )
