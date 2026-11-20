"""所有設定集中在這裡。其他模組只讀 Config 物件，不直接讀環境變數。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# 專案根目錄：資料（faces/、face_feats.npz、id_map.txt、models/）都放在這裡
PROJECT_ROOT = Path(__file__).resolve().parent.parent

GESTURE_MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/gesture_recognizer/'
                     'gesture_recognizer/float16/latest/gesture_recognizer.task')

# 人臉模型（OpenCV Zoo，Apache-2.0）：檔名 → (下載網址, SHA-256)。順序固定：偵測、辨識
FACE_MODELS = {
    'face_detection_yunet_2023mar.onnx': (
        'https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx',
        '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
    'face_recognition_sface_2021dec.onnx': (
        'https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx',
        '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
}

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
    min_face: Tuple[int, int] = (80, 80)
    capture_count: int = 30
    threshold: float = 50.0                       # SFace 距離 =（1 − cosine）× 100，越低越像；50 = cosine 0.5
    face_score: float = 0.6                       # YuNet 偵測信心值，低於此不算一張臉
    required_frames: int = 8                      # 同一人連續通過幾幀才算通過
    rearm_seconds: float = 3.0                    # 人離開鏡頭幾秒後才允許再次自動開鎖
    report_margin: float = 3.0                    # 回報誤認後，門檻比最像的冒充者再嚴格多少（SFace 距離）
    report_floor: float = 35.0                    # 門檻最低收到這裡（cosine 0.65）；再低本人也常過不了，應該重拍

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
    gesture_wrong_frames: int = 15                # 這次驗證中，比錯手勢累計幾幀就直接失敗（防止輪流試）
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
    web_https: bool = True                        # 自簽憑證 HTTPS（手機用麥克風對門口講話一定要 HTTPS）；同一個埠的 http 會轉到 https
    tls_cert: Optional[Path] = None               # None = 專案資料夾/certs/smartlock.crt（沒有就自動產生）
    tls_key: Optional[Path] = None
    simulate: bool = False                        # True = 不接 Arduino，用文字模擬

    # ---- 哨兵（陌生人錄影） ----
    sentinel: bool = True
    sentinel_trigger: float = 5.0                 # 陌生人連續出現幾秒才開始錄影
    sentinel_stop: float = 5.0                    # 陌生人離開幾秒後停止
    sentinel_max: float = 60.0                    # 單段影片最長秒數（超過就切成下一段）
    sentinel_preroll: float = 5.0                 # 觸發前保留幾秒畫面
    sentinel_fps: float = 10.0                    # 錄影幀率（省空間）
    sentinel_cooldown: float = 300.0              # 兩次「建議通知」最短間隔（秒）
    sentinel_min_face: int = 80                   # 臉寬至少幾 px 才算（排除遠處路人）

    # ---- 紀錄保存（SD 卡容量管理） ----
    record_dir: Optional[Path] = None             # None = 專案資料夾/records
    record_max_mb: float = 2048.0                 # 錄影總容量上限，超過刪最舊的
    record_max_days: float = 7.0                  # 錄影保留天數
    record_min_free_mb: float = 1024.0            # SD 卡剩餘空間低於此值時先刪舊檔，仍不足就不錄
    event_keep_days: float = 90.0                 # 文字事件紀錄保留天數

    # ---- LINE ----
    line_token: str = ''                          # Channel access token（放 smartlock.env，不要寫進程式）
    line_basic_id: str = ''                       # 官方帳號 ID，例如 @your_line_id（加好友 QR 用）
    line_user_id: str = ''                        # 有設定就只推給這個人，沒設定就廣播給所有好友
    line_notify: bool = True                      # 有 token 時是否發通知（0 = 只顯示額度）
    line_cooldown: float = 300.0                  # 同一位訪客 / 開鎖失敗，幾秒內只通知一次

    # ---- 對講機 ----
    intercom: str = 'auto'                        # auto（有 USB 音效卡就啟用）| 1 | 0 | sim（Mac 開發用測試音）
    audio_device: str = ''                        # 例如 plughw:2,0；空白 = 自動找 USB 音效卡
    intercom_channel: str = 'both'                # 對講聲音（說話、預錄）從哪個聲道播：both（兩邊都響）| left（門外）| right（室內）
    speaker_volume: int = 100                     # 啟動時把 USB 卡喇叭音量設成幾 %（這張卡預設 62% 幾乎沒聲音）
    talk_max_seconds: float = 120.0               # 按住說話單次最長秒數

    # ---- 路徑 ----
    @property
    def cert_file(self) -> Path:
        return Path(self.tls_cert) if self.tls_cert else self.base / 'certs' / 'smartlock.crt'

    @property
    def key_file(self) -> Path:
        return Path(self.tls_key) if self.tls_key else self.base / 'certs' / 'smartlock.key'

    @property
    def gestures_file(self) -> Path:
        return self.base / 'gestures.json'

    @property
    def sounds_dir(self) -> Path:
        return self.base / 'sounds'

    @property
    def pin_file(self) -> Path:
        return self.base / 'pin.json'

    @property
    def records_path(self) -> Path:
        return Path(self.record_dir) if self.record_dir else self.base / 'records'

    @property
    def face_dir(self) -> Path:
        return self.base / 'faces'

    @property
    def models_dir(self) -> Path:
        return self.base / 'models'

    @property
    def feature_file(self) -> Path:
        return self.base / 'face_feats.npz'

    @property
    def impostor_dir(self) -> Path:
        return self.base / 'impostors'

    @property
    def thresholds_file(self) -> Path:
        return self.base / 'thresholds.json'

    @property
    def map_file(self) -> Path:
        return self.base / 'id_map.txt'

    @property
    def gesture_model(self) -> Path:
        return self.models_dir / 'gesture_recognizer.task'

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
            threshold=float(env.get('FACE_THRESHOLD', 50)),
            face_score=float(env.get('FACE_SCORE', 0.6)),
            report_margin=float(env.get('FACE_REPORT_MARGIN', 3)),
            report_floor=float(env.get('FACE_REPORT_FLOOR', 35)),
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
            gesture_wrong_frames=int(env.get('GESTURE_WRONG_FRAMES', 15)),
            gesture_window=float(env.get('GESTURE_WINDOW', 8)),
            web_port=int(env.get('WEB_PORT', 5050)),
            enable_qr=_flag(env, 'ENABLE_QR', '1'),
            show_window=env.get('SHOW_WINDOW', 'auto'),
            kiosk=_flag(env, 'KIOSK', '0'),
            control_pin=env.get('CONTROL_PIN', ''),
            public_url=env.get('PUBLIC_URL', ''),
            web_https=_flag(env, 'WEB_HTTPS', '1'),
            tls_cert=Path(env['TLS_CERT']) if env.get('TLS_CERT') else None,
            tls_key=Path(env['TLS_KEY']) if env.get('TLS_KEY') else None,
            simulate=_flag(env, 'SIM_ARDUINO', '0'),
            sentinel=_flag(env, 'SENTINEL', '1'),
            sentinel_trigger=float(env.get('SENTINEL_TRIGGER', 5)),
            sentinel_stop=float(env.get('SENTINEL_STOP', 5)),
            sentinel_max=float(env.get('SENTINEL_MAX', 60)),
            sentinel_preroll=float(env.get('SENTINEL_PREROLL', 5)),
            sentinel_fps=float(env.get('SENTINEL_FPS', 10)),
            sentinel_cooldown=float(env.get('SENTINEL_COOLDOWN', 300)),
            sentinel_min_face=int(env.get('SENTINEL_MIN_FACE', 80)),
            record_dir=Path(env['RECORD_DIR']) if env.get('RECORD_DIR') else None,
            record_max_mb=float(env.get('RECORD_MAX_MB', 2048)),
            record_max_days=float(env.get('RECORD_MAX_DAYS', 7)),
            record_min_free_mb=float(env.get('RECORD_MIN_FREE_MB', 1024)),
            event_keep_days=float(env.get('EVENT_KEEP_DAYS', 90)),
            line_token=env.get('LINE_CHANNEL_TOKEN', ''),
            line_basic_id=env.get('LINE_BASIC_ID', ''),
            line_user_id=env.get('LINE_USER_ID', ''),
            line_notify=env.get('LINE_NOTIFY', '1') != '0',
            line_cooldown=float(env.get('LINE_COOLDOWN', 300)),
            intercom=env.get('INTERCOM', 'auto'),
            audio_device=env.get('AUDIO_DEVICE', ''),
            intercom_channel=env.get('INTERCOM_CHANNEL', 'both'),
            speaker_volume=max(0, min(100, int(env.get('SPEAKER_VOLUME', 100)))),
            talk_max_seconds=float(env.get('TALK_MAX_SECONDS', 120)),
        )
