"""樹莓派人臉 + 手勢雙重驗證門鎖：Pi 5 + Pi 相機 + MediaPipe 手勢 + USB 連接 Arduino 控制繼電器。

身分：master 可自動開鎖；guest 只會通知「偵測到訪客，建議開鎖」，由人在網頁上決定要不要開。
開鎖流程（master）：認出已登錄的人臉 → 畫面提示比手勢 → 在時限內比出指定手勢（預設 讚 或 YA）→ 開鎖。
執行環境：Python 3.9–3.12（MediaPipe 尚無 Python 3.13 的 ARM 版），詳見 SETUP.md。

用法（在 app.py 所在資料夾）：
  python3 app.py                 互動選單
  python3 app.py capture yang    採集 yang 的人臉 30 張
  python3 app.py train           以 faces/ 訓練 LBPH 模型
  python3 app.py run             啟動辨識 + 網頁控制台 + Arduino 控制
  python3 app.py run --sim       同上，但不接 Arduino，用文字模擬開鎖/上鎖
  python3 app.py test-camera     只測相機：FPS、人臉偵測、辨識距離，存 camera_test.jpg
  python3 app.py test-arduino    只測試 Arduino：上鎖 → 解鎖 → 等待 8 秒自動上鎖
  python3 app.py test-gesture    只測手勢：即時顯示偵測到的手勢與信心值，存 gesture_test.jpg
  python3 app.py qr              在終端機印出控制頁網址的 QR code

資料沿用舊版格式：faces/<ID>_*.jpg、face.yml、id_map.txt（編號:名字:master|guest）。
"""
import glob
import hmac
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

try:
    import serial
except ImportError:          # 沒裝 pyserial 時仍可採集、訓練
    serial = None

# ==========================================
# 設定（可用環境變數覆寫，例如 FACE_THRESHOLD=60 python3 app.py run）
# ==========================================
BASE = Path(__file__).resolve().parent
FACE_DIR = BASE / 'faces'
MODEL_FILE = BASE / 'face.yml'
MAP_FILE = BASE / 'id_map.txt'

FRAME_SIZE = (640, 480)
FACE_SIZE = (200, 200)          # 訓練與辨識前統一尺寸
MIN_FACE = (80, 80)
CAPTURE_COUNT = 30
THRESHOLD = float(os.environ.get('FACE_THRESHOLD', 70))   # LBPH 距離，越低越像
REQUIRED_FRAMES = 8             # 同一人連續通過幾幀才解鎖
REARM_SECONDS = 3               # 人離開鏡頭幾秒後才允許再次自動解鎖
ARDUINO_PORT = os.environ.get('ARDUINO_PORT')             # 不設定則自動尋找
BAUD = int(os.environ.get('ARDUINO_BAUD', 9600))           # 需與 Arduino 的 Serial.begin 一致
# 送給 Arduino 的文字，預設與學弟在電腦 Serial Monitor 輸入的相同
CMD_TEXT = {'UNLOCK': os.environ.get('CMD_UNLOCK', 'unlock'),
            'LOCK': os.environ.get('CMD_LOCK', 'lock')}
CMD_EOL = {'lf': '\n', 'crlf': '\r\n', 'cr': '\r', 'none': ''}[os.environ.get('CMD_EOL', 'lf')]
# 1（預設）= 必須收到含 unlock/lock 字樣的回覆才算成功（學弟的 sketch 會回 Unlocked.../Locked）
# 0 = 送出即算成功，適用於不回覆文字的 Arduino 程式
ACK_STRICT = os.environ.get('ARDUINO_STRICT', '1') == '1'
UNLOCK_SECONDS = 8              # 與 door_lock.ino 的 UNLOCK_DURATION（8000 ms）一致；只用於模擬與測試顯示
WEB_PORT = int(os.environ.get('WEB_PORT', 5050))

# ---- 相機 ----
# auto：Linux 上有 rpicam-vid 就用 Pi 相機，其次 Picamera2，最後 USB/內建 webcam（Mac）
CAMERA = os.environ.get('CAMERA', 'auto')            # auto | rpicam | picamera2 | usb
CAMERA_INDEX = int(os.environ.get('CAMERA_INDEX', 0))  # USB / Mac 鏡頭編號；Mac 接續互通相機可能讓 0 變成 iPhone

# ---- 手勢（MediaPipe）----
REQUIRE_GESTURE = os.environ.get('REQUIRE_GESTURE', '1') == '1'    # 0 = 關閉手勢，只用人臉開門（舊版行為）
ALLOWED_GESTURES = [g.strip() for g in os.environ.get('ALLOWED_GESTURES', 'Thumb_Up,Victory').split(',') if g.strip()]
GESTURE_SCORE = float(os.environ.get('GESTURE_SCORE', 0.6))      # 手勢信心值門檻（0–1）
GESTURE_FRAMES = int(os.environ.get('GESTURE_FRAMES', 5))        # 連續幾幀都是同一個合格手勢才算數
GESTURE_WINDOW = float(os.environ.get('GESTURE_WINDOW', 8))      # 人臉通過後，幾秒內要比出手勢
VISITOR_HOLD = 10.0            # 訪客離開鏡頭後，網頁提示保留幾秒
FACE_GRACE = 3.0                # 比手勢時手可能擋到臉；人臉消失超過這麼久才取消
RETRY_COOLDOWN = 3.0            # 手勢逾時後，隔幾秒才重新開始
GESTURE_MODEL = BASE / 'models' / 'gesture_recognizer.task'
GESTURE_MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/gesture_recognizer/'
                     'gesture_recognizer/float16/latest/gesture_recognizer.task')
# ---- 門口顯示視窗 / QR code / PIN ----
ENABLE_QR = True        # False = 不顯示 QR code（也不需要安裝 qrcode 套件）；預覽視窗照樣有
SHOW_WINDOW = os.environ.get('SHOW_WINDOW', 'auto')   # auto = 有螢幕就開；1 = 強制開；0 = 不開
KIOSK = os.environ.get('KIOSK', '0') == '1'           # 1 = 視窗全螢幕
CONTROL_PIN = os.environ.get('CONTROL_PIN', '')       # 設定後，網頁按開鎖/上鎖都要輸入這組 PIN
PUBLIC_URL = os.environ.get('PUBLIC_URL', '')         # QR code 要導向的網址；不設定就自動用 http://<區網 IP>:<埠>

# 純文字標籤（Pi 的終端機 / 瀏覽器預設沒有 emoji 字型）
GESTURE_LABEL = {'Thumb_Up': '讚(Thumb_Up)', 'Thumb_Down': '倒讚(Thumb_Down)', 'Victory': 'YA(Victory)',
                 'Open_Palm': '張開手(Open_Palm)', 'Closed_Fist': '握拳(Closed_Fist)',
                 'Pointing_Up': '食指朝上(Pointing_Up)', 'ILoveYou': 'ILoveYou'}


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


# ==========================================
# 人臉工具
# ==========================================
def load_detector():
    name = 'haarcascade_frontalface_default.xml'
    candidates = [BASE / name]
    if hasattr(cv2, 'data'):    # pip 版 OpenCV 才有 cv2.data
        candidates.append(Path(cv2.data.haarcascades) / name)
    candidates += [Path(p) for p in glob.glob(f'/usr/share/opencv*/haarcascades/{name}')]
    for path in candidates:
        if path.exists():
            detector = cv2.CascadeClassifier(str(path))
            if not detector.empty():
                return detector
    raise RuntimeError(f'找不到 {name}：把舊專案裡的這個檔案放到 app.py 同一層，'
                       '或 sudo apt install opencv-data')


def new_recognizer():
    if not hasattr(cv2, 'face'):
        raise RuntimeError('OpenCV 缺少 cv2.face：請 sudo apt install python3-opencv，'
                           '或 pip 安裝 opencv-contrib-python')
    return cv2.face.LBPHFaceRecognizer_create()


def normalize(face_gray):
    return cv2.equalizeHist(cv2.resize(face_gray, FACE_SIZE))


def read_map():
    """回傳 {編號: (名字, 類別)}"""
    result = {}
    if MAP_FILE.exists():
        for line in MAP_FILE.read_text(encoding='utf-8').splitlines():
            parts = line.strip().split(':')
            if len(parts) == 3 and parts[0].isdigit():
                result[int(parts[0])] = (parts[1], parts[2])
    return result


def has_display():
    if sys.platform == 'darwin':          # macOS 沒有 DISPLAY 變數，但一定有桌面
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def lan_ip():
    """取得本機在區網的 IP（不會真的送出封包）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return subprocess.run(['hostname', '-I'], capture_output=True, text=True).stdout.split()[0]
        except (OSError, IndexError):
            return '127.0.0.1'


def control_url():
    return PUBLIC_URL or f'http://{lan_ip()}:{WEB_PORT}'


def qr_matrix(text):
    """回傳 QR code 的布林矩陣（含白邊）；沒有安裝 qrcode 套件時回傳 None。"""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def print_qr(text):
    """在終端機用 Unicode 半格字元印出 QR code（SSH 也能顯示）。"""
    m = qr_matrix(text)
    if m is None:
        if shutil.which('qrencode'):
            subprocess.run(['qrencode', '-t', 'ansiutf8', text])
        else:
            print('（沒有 qrcode 套件，無法印出 QR code：pip install qrcode）')
        print(text)
        return
    if len(m) % 2:
        m = m + [[False] * len(m[0])]
    # 以「亮底暗格」輸出：深色終端機背景下手機也能掃
    chars = {(False, False): '█', (True, True): ' ', (True, False): '▄', (False, True): '▀'}
    lines = [''.join(chars[(top, bottom)] for top, bottom in zip(m[r], m[r + 1])) for r in range(0, len(m), 2)]
    print('\n'.join(lines))
    print(text, flush=True)


def qr_image(text, size):
    """回傳 size×size 的 BGR QR code 圖片；沒有 qrcode 套件時回傳 None。"""
    m = qr_matrix(text)
    if m is None:
        return None
    img = np.where(np.array(m, dtype=bool), 0, 255).astype(np.uint8)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


# ==========================================
# 相機：Pi 相機（rpicam-vid 或 Picamera2）或 USB / Mac 內建 webcam
# ==========================================
class RpicamCamera:
    """用 rpicam-vid 把 Pi 相機的 YUV420 原始畫面輸出到 stdout，背景執行緒持續讀最新一幀。
    不需要 Picamera2 / libcamera 的 Python 套件，所以任何 Python 版本（含 MediaPipe 用的 3.11）都能用。"""

    def __init__(self, size=FRAME_SIZE):
        self.w, self.h = size
        self.frame_bytes = self.w * self.h * 3 // 2
        cmd = ['rpicam-vid', '-t', '0', '-n', '--codec', 'yuv420', '--width', str(self.w),
               '--height', str(self.h), '--framerate', '30', '--flush', '-o', '-']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.latest = None
        self.seq = 0
        self.cond = threading.Condition()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        deadline = time.monotonic() + 8
        with self.cond:
            while self.latest is None and time.monotonic() < deadline and self.proc.poll() is None:
                self.cond.wait(0.2)
        if self.latest is None:
            self.close()
            raise RuntimeError('rpicam-vid 沒有輸出畫面：確認 rpicam-hello --list-cameras 看得到相機，'
                               '且沒有其他程式正在使用相機')

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _reader(self):
        while self.alive:
            raw = self._read_exact(self.frame_bytes)
            if raw is None:
                break
            yuv = np.frombuffer(raw, np.uint8).reshape(self.h * 3 // 2, self.w)
            frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            with self.cond:
                self.latest = frame
                self.seq += 1
                self.cond.notify_all()
        self.alive = False
        with self.cond:
            self.cond.notify_all()

    def read(self):
        """回傳比上一次更新的畫面（等最多 1 秒），讀不到回傳 None。"""
        with self.cond:
            last = getattr(self, '_last_seq', 0)
            self.cond.wait_for(lambda: self.seq != last or not self.alive, timeout=1.0)
            if self.seq == last:
                return None
            self._last_seq = self.seq
            return self.latest.copy()

    def close(self):
        self.alive = False
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Camera:
    def __init__(self):
        self.impl = None
        self.picam = None
        self.cap = None
        want = CAMERA
        if want in ('auto', 'rpicam') and sys.platform.startswith('linux') and shutil.which('rpicam-vid'):
            try:
                self.impl = RpicamCamera()
                log('相機：Pi 相機（rpicam-vid）')
                return
            except RuntimeError as exc:
                if want == 'rpicam':
                    raise
                log(f'rpicam-vid 失敗（{exc}），改試其他方式')
        if want in ('auto', 'picamera2'):
            try:
                from picamera2 import Picamera2
                if Picamera2.global_camera_info():
                    self.picam = Picamera2()
                    self.picam.configure(self.picam.create_video_configuration(
                        main={'size': FRAME_SIZE, 'format': 'RGB888'}))      # RGB888 的記憶體順序即 BGR
                    self.picam.start()
                    time.sleep(1)
                    log('相機：Pi 相機（Picamera2）')
                    return
            except ImportError:
                if want == 'picamera2':
                    raise RuntimeError('這個 Python 環境沒有 picamera2')
        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        if not self.cap.isOpened():
            if sys.platform == 'darwin':
                raise RuntimeError(f'鏡頭 {CAMERA_INDEX} 無法開啟：到「系統設定 → 隱私權與安全性 → 相機」允許終端機 App，'
                                   '重開終端機；或試 CAMERA_INDEX=1')
            raise RuntimeError('鏡頭無法開啟：先用 rpicam-hello --list-cameras 確認相機，'
                               '並關閉其他使用相機的程式')
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_SIZE[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_SIZE[1])
        log(f'相機：USB / 內建 webcam（編號 {CAMERA_INDEX}）')

    def read(self):
        if self.impl is not None:
            return self.impl.read()
        if self.picam is not None:
            return self.picam.capture_array()
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        if self.impl is not None:
            self.impl.close()
        if self.picam is not None:
            self.picam.stop()
            self.picam.close()
        if self.cap is not None:
            self.cap.release()


def read_frame(camera, failures):
    """讀一幀；連續失敗過多時丟出錯誤，避免像舊版一樣無聲卡死。"""
    frame = camera.read()
    if frame is None:
        failures[0] += 1
        if failures[0] >= 50:
            raise RuntimeError('鏡頭連續 50 次讀不到畫面')
        time.sleep(0.05)
    else:
        failures[0] = 0
    return frame


# ==========================================
# 手勢辨識（MediaPipe）
# ==========================================
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
                    (17, 18), (18, 19), (19, 20)]


def classify_landmarks(pts):
    """備援用：以 MediaPipe Hands 的 21 個關鍵點（正規化座標，y 向下）判斷手勢。
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


def ensure_gesture_model():
    """沒有模型檔就自動下載（約 8 MB）；失敗回傳 False，改用備援模式。"""
    if GESTURE_MODEL.exists() and GESTURE_MODEL.stat().st_size > 100_000:
        return True
    try:
        import ssl
        import urllib.request
        try:                                   # macOS 的 python.org 版 Python 常缺根憑證，改用 certifi
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        GESTURE_MODEL.parent.mkdir(exist_ok=True)
        log('下載 MediaPipe 手勢模型...')
        tmp = GESTURE_MODEL.with_suffix('.part')
        with urllib.request.urlopen(GESTURE_MODEL_URL, context=ctx, timeout=30) as resp, open(tmp, 'wb') as out:
            shutil.copyfileobj(resp, out)
        tmp.rename(GESTURE_MODEL)
        log(f'手勢模型已存到 {GESTURE_MODEL}')
        return True
    except Exception as exc:
        GESTURE_MODEL.with_suffix('.part').unlink(missing_ok=True)
        log(f'手勢模型下載失敗（{exc}），改用備援模式：MediaPipe Hands + 關鍵點規則')
        log(f'  可以手動下載：curl -L -o {GESTURE_MODEL} {GESTURE_MODEL_URL}')
        return False


class GestureDetector:
    """優先使用 MediaPipe 官方 Gesture Recognizer；沒有模型檔時改用 MediaPipe Hands + 規則判斷。"""

    def __init__(self):
        try:
            import mediapipe as mp
        except ImportError:
            v = f'{sys.version_info.major}.{sys.version_info.minor}'
            raise RuntimeError(f'目前的 Python {v} 沒有 mediapipe：請在 Python 3.9–3.12 的 venv 執行 '
                               'pip install -r requirements.txt（見 SETUP.md）。'
                               '確認有先 source .venv/bin/activate')
        self.mp = mp
        self.recognizer = None
        self.hands = None
        self.last_ts = -1
        self.t0 = time.monotonic()
        if ensure_gesture_model():
            try:
                from mediapipe.tasks.python import BaseOptions, vision
                options = vision.GestureRecognizerOptions(
                    base_options=BaseOptions(model_asset_path=str(GESTURE_MODEL)),
                    running_mode=vision.RunningMode.VIDEO, num_hands=1,
                    min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5,
                    min_tracking_confidence=0.5)
                self.recognizer = vision.GestureRecognizer.create_from_options(options)
                self.mode = 'MediaPipe Gesture Recognizer'
            except Exception as exc:
                log(f'手勢模型載入失敗（{exc}）：刪除 {GESTURE_MODEL} 後重新執行可重新下載；先改用備援模式')
        if self.recognizer is None:
            self.hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=1,
                                                  model_complexity=0, min_detection_confidence=0.6,
                                                  min_tracking_confidence=0.5)
            self.mode = 'MediaPipe Hands + 規則'
        log(f'手勢辨識：{self.mode}')

    def detect(self, frame_bgr):
        """回傳 (手勢名稱或 None, 信心值, 21 個關鍵點的像素座標或 None)"""
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


def draw_bar(img, x, y, w, h, frac, color):
    cv2.rectangle(img, (x, y), (x + w, y + h), (60, 60, 60), -1)
    cv2.rectangle(img, (x, y), (x + int(w * max(0.0, min(1.0, frac))), y + h), color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (230, 230, 230), 1)


def draw_label(img, text, org, scale=0.7, color=(255, 255, 255), bg=(0, 0, 0)):
    """有底色的文字，在亮背景上也看得清楚（OpenCV 只能畫英文）。"""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x, y = org
    cv2.rectangle(img, (x - 4, y - th - 6), (x + tw + 4, y + base + 2), bg, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


# ==========================================
# 1. 採集
# ==========================================
def capture(user_id, count=CAPTURE_COUNT):
    if not re.fullmatch(r'[A-Za-z0-9-]+', user_id or ''):
        raise ValueError('ID 只能用英文、數字、減號（不可含底線或空格）')
    detector = load_detector()
    FACE_DIR.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d%H%M%S')        # 每次採集不同檔名，不會蓋掉舊照片
    show = has_display()
    camera = Camera()
    saved, last_save, failures, just_saved_until = 0, 0.0, [0], 0.0
    log(f'開始採集 {user_id}：請正對鏡頭並稍微轉動角度' + ('（按 q 結束）' if show else ''))
    try:
        while saved < count:
            frame = read_frame(camera, failures)
            if frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, 1.2, 5, minSize=MIN_FACE)
            if len(faces) == 1 and time.monotonic() - last_save >= 0.2:
                x, y, w, h = faces[0]
                if cv2.imwrite(str(FACE_DIR / f'{user_id}_{stamp}-{saved}.jpg'), gray[y:y + h, x:x + w]):
                    saved += 1
                    last_save = time.monotonic()
                    just_saved_until = last_save + 0.12
                    print(f'\r已採集 {saved}/{count}', end='', flush=True)
            if show:
                one = len(faces) == 1
                for x, y, w, h in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0) if one else (0, 0, 255), 2)
                if just_saved_until > time.monotonic() and one:          # 剛拍到時框線閃白
                    x, y, w, h = faces[0]
                    cv2.rectangle(frame, (x - 4, y - 4), (x + w + 4, y + h + 4), (255, 255, 255), 2)
                hint = ('Turn your head slowly' if one else
                        'No face - look at the camera' if len(faces) == 0 else 'Only ONE face please')
                draw_label(frame, f'Capturing: {user_id}', (12, 30), 0.75)
                draw_label(frame, hint, (12, 62), 0.6, (255, 255, 255), (0, 120, 0) if one else (0, 0, 160))
                fh, fw = frame.shape[:2]
                draw_bar(frame, 12, fh - 40, fw - 24, 22, saved / count, (0, 200, 0))
                draw_label(frame, f'{saved}/{count}', (fw // 2 - 30, fh - 50), 0.7)
                cv2.imshow('Capture (q = quit)', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    finally:
        camera.close()
        if show:
            cv2.destroyAllWindows()
    print()
    if show and saved >= count:                       # 最後一張停留一下，讓人看到 30/30
        time.sleep(0.3)
    log(f'採集完成：{saved} 張。接著執行 train 重新訓練')


# ==========================================
# 2. 訓練
# ==========================================
def train():
    if not FACE_DIR.exists():
        raise RuntimeError('沒有 faces 資料夾，請先採集')
    old_category = {name: cat for name, cat in read_map().values()}
    samples, ids, labels, per_person = [], [], {}, {}
    for path in sorted(FACE_DIR.iterdir()):
        if path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
            continue
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            continue
        name = path.stem.split('_')[0]
        # faces/ 裡已是裁切好的人臉，不再重新偵測（舊版重新偵測常導致 0 張可用）
        samples.append(normalize(img))
        ids.append(labels.setdefault(name, len(labels)))
        per_person[name] = per_person.get(name, 0) + 1
    if not samples:
        raise RuntimeError('faces 資料夾沒有照片，請先採集')
    model = new_recognizer()
    model.train(samples, np.asarray(ids, dtype=np.int32))
    model.write(str(MODEL_FILE))
    # 保留手動改過的 guest 設定，新的人預設 master
    MAP_FILE.write_text(''.join(f'{num}:{name}:{old_category.get(name, "master")}\n'
                                for name, num in labels.items()), encoding='utf-8')
    log(f'訓練完成：共 {len(samples)} 張 → ' + '、'.join(f'{n} {c} 張' for n, c in per_person.items()))


# ==========================================
# 3. Arduino 控制（USB serial，預設送 unlock / lock + 換行，與 Serial Monitor 手動輸入相同）
# ==========================================
class Arduino:

    def __init__(self, port=ARDUINO_PORT, baud=BAUD):
        self.port_setting = port
        self.port = None
        self.baud = baud
        self.ser = None
        self.lock = threading.RLock()
        self.status = '未連線'

    def find_port(self):
        if self.port_setting:
            return self.port_setting
        for pattern in ('/dev/serial/by-id/*', '/dev/ttyACM*', '/dev/ttyUSB*',
                        '/dev/cu.usbmodem*', '/dev/cu.usbserial*', '/dev/cu.wchusbserial*'):   # 後三個是 macOS
            found = sorted(glob.glob(pattern))
            if found:
                return found[0]
        return None

    @property
    def connected(self):
        return self.ser is not None

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @staticmethod
    def _matches(command, line):
        low = line.lower()
        if command == 'UNLOCK':
            return 'unlock' in low or '開' in line
        return ('lock' in low and 'unlock' not in low) or '關' in line or '上鎖' in line

    def _exchange(self, command, timeout=1.0):
        """送出指令並收集回覆。回傳 (是否確認, 回覆文字列表)。"""
        self.ser.reset_input_buffer()            # 丟掉 Arduino 先前主動送出的訊息
        self.ser.write((CMD_TEXT[command] + CMD_EOL).encode('utf-8'))
        self.ser.flush()
        replies = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.ser.readline().decode('utf-8', errors='replace').strip()
            if not line:
                continue
            replies.append(line)
            if self._matches(command, line):
                return True, replies
        return False, replies

    def connect(self):
        with self.lock:
            self._close()
            if serial is None:
                self.status = '缺少 pyserial（sudo apt install python3-serial）'
                return False
            port = self.find_port()
            if port is None:
                self.status = '找不到 Arduino（檢查 USB 線是否可傳資料）'
                return False
            try:
                self.ser = serial.Serial(port, self.baud, timeout=0.2, write_timeout=1)
                time.sleep(2)                    # 開啟序列埠會讓 UNO 重新開機
                confirmed, replies = self._exchange('LOCK')   # 用「上鎖」當握手：最安全的狀態
                if confirmed or not ACK_STRICT:
                    self.port = port
                    note = '' if confirmed else (f'，回覆：{" / ".join(replies)}' if replies else '，Arduino 沒有回覆文字')
                    self.status = f'已連線 {port}{note}'
                    log(f'Arduino {self.status}')
                    return True
                self.status = (f'{port} 有開啟但 Arduino 沒確認（回覆：{replies or "無"}）：'
                               f'檢查 baud={self.baud}、指令文字與 Arduino 程式')
            except (serial.SerialException, OSError) as exc:
                self.status = f'連線失敗：{exc}'
            self._close()
            if self.status != getattr(self, '_last_logged', None):
                log(f'Arduino {self.status}')
                self._last_logged = self.status
            return False

    def send(self, command):
        """回傳 (成功與否, 訊息)"""
        with self.lock:
            if self.ser is None and not self.connect():
                return False, self.status
            try:
                text = CMD_TEXT[command]
                confirmed, replies = self._exchange(command)
                reply = ' / '.join(replies) if replies else '無回覆'
                if confirmed:
                    return True, f'已送出 {text}，Arduino 回覆：{reply}'
                if not ACK_STRICT:
                    return True, f'已送出 {text}（Arduino 回覆：{reply}）'
                return False, f'已送出 {text}，但 Arduino 未確認（{reply}）'
            except (serial.SerialException, OSError) as exc:
                self._close()
                self.status = f'連線中斷：{exc}'
                return False, self.status

    def watchdog(self, stop):
        """背景執行：USB 拔掉時標示斷線，插回後自動重連。"""
        while not stop.wait(3):
            with self.lock:
                if self.ser is not None and self.port and not os.path.exists(self.port):
                    self._close()
                    self.status = 'USB 已拔除，等待重新連線'
                    log(self.status)
                elif self.ser is None:
                    self.connect()

    def close(self):
        with self.lock:
            self._close()


class FakeArduino:
    """模擬模式：不接 Arduino，用文字顯示門鎖動作；行為與 door_lock.ino 相同（解鎖後自動上鎖）。"""
    UNLOCK_SECONDS = UNLOCK_SECONDS

    def __init__(self):
        self.status = '模擬模式（未接 Arduino）'
        self.connected = True
        self.lock = threading.Lock()
        self.timer = None

    def connect(self):
        log('[模擬 Arduino] 已就緒 → 門鎖：上鎖')
        return True

    def _auto_lock(self):
        log(f'[模擬 Arduino] {self.UNLOCK_SECONDS} 秒到 → 自動上鎖 [LOCKED]')

    def send(self, command):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if command == 'UNLOCK':
                log(f'[模擬 Arduino] 繼電器 ON → 開鎖 [UNLOCKED]（{self.UNLOCK_SECONDS} 秒後自動上鎖）')
                self.timer = threading.Timer(self.UNLOCK_SECONDS, self._auto_lock)
                self.timer.daemon = True
                self.timer.start()
                return True, '[模擬] Arduino 開鎖'
            log('[模擬 Arduino] 繼電器 OFF → 上鎖 [LOCKED]')
            return True, '[模擬] Arduino 上鎖'

    def watchdog(self, stop):
        stop.wait()

    def close(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()


# ==========================================
# 4. 辨識 + 網頁 + 門鎖
# ==========================================
class DoorSystem:
    def __init__(self, arduino):
        self.arduino = arduino
        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.jpeg = None
        self.frame = None                    # 最新一幀（已畫框），給門口顯示視窗用
        self.unlocked_until = 0.0            # 推估的開鎖結束時間（Arduino 自動上鎖秒數）
        self.events = deque(maxlen=12)
        self.state = {'name': '無人臉', 'category': '', 'distance': None,
                      'auto': True, 'message': '啟動中',
                      'require_gesture': REQUIRE_GESTURE, 'phase': 'face', 'countdown': None,
                      'gesture': None, 'gesture_score': None, 'visitor': None, 'visitor_since': None,
                      'allowed': [GESTURE_LABEL.get(g, g) for g in ALLOWED_GESTURES]}

    def update(self, **values):
        with self.guard:
            self.state.update(values)

    def event(self, text):
        log(text)
        with self.guard:
            self.events.appendleft(f'{time.strftime("%H:%M:%S")} {text}')

    def snapshot(self):
        with self.guard:
            data = dict(self.state)
            data['events'] = list(self.events)
        data['arduino'] = self.arduino.status
        data['connected'] = self.arduino.connected
        data['pin_required'] = bool(CONTROL_PIN)
        data['unlocked'] = time.monotonic() < self.unlocked_until
        return data

    def door(self, command, reason):
        ok, msg = self.arduino.send(command)
        if ok:
            self.unlocked_until = time.monotonic() + UNLOCK_SECONDS if command == 'UNLOCK' else 0.0
            if command == 'UNLOCK':
                self.update(visitor=None, visitor_since=None)
        self.update(message=msg)
        self.event(f'{reason} → {command} {"成功" if ok else "失敗：" + msg}')
        return ok, msg

    def recognition_loop(self, camera, model, mapping, gesture=None):
        """狀態機：
        face    ：等待已登錄的人臉連續通過 REQUIRED_FRAMES 幀
        gesture ：人臉通過後，GESTURE_WINDOW 秒內比出 ALLOWED_GESTURES 之一（連續 GESTURE_FRAMES 幀）→ 開鎖
        REQUIRE_GESTURE=0 或沒有手勢偵測器時，人臉通過就直接開鎖（舊版行為）。"""
        candidate, streak, armed, absent_since, failures = None, 0, True, None, [0]
        last_label = None
        phase, target, target_name, deadline, last_seen = 'face', None, '', 0.0, 0.0
        g_streak, g_last, cooldown_until = 0, None, 0.0
        v_label, v_streak, v_seen, v_notified = None, 0, 0.0, set()     # 訪客（guest）偵測
        try:
            detector = load_detector()
            while not self.stop.is_set():
                frame = read_frame(camera, failures)
                if frame is None:
                    continue
                now = time.monotonic()
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(gray, 1.2, 5, minSize=MIN_FACE)
                accepted, name, category, best = None, '無人臉', '', None
                guest_label = None
                for x, y, w, h in faces:
                    label, distance = model.predict(normalize(gray[y:y + h, x:x + w]))
                    person, kind = mapping.get(label, ('Unknown', 'Stranger'))
                    known = distance < THRESHOLD and kind in ('master', 'guest')
                    color = ((0, 255, 0) if kind == 'master' else (200, 0, 200)) if known else (0, 0, 255)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    tag = (person if kind == 'master' else f'{person} (guest)') if known else 'Unknown'
                    cv2.putText(frame, f'{tag} {distance:.0f}',
                                (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    if len(faces) == 1:
                        name, category, best = (person, kind, distance) if known else ('Unknown', 'Stranger', distance)
                        accepted = label if known and kind == 'master' else None      # 只有 master 能自動開鎖
                        guest_label = label if known and kind == 'guest' else None
                if len(faces) > 1:
                    name = '多人入鏡，暫不自動解鎖'
                if name != last_label and name not in ('無人臉',):
                    self.event(f'看到 {name} {category}'.strip())
                last_label = name

                # 訪客：連續認出 REQUIRED_FRAMES 幀就通知一次；離開 REARM_SECONDS 秒後可再通知
                if guest_label is not None:
                    v_streak = v_streak + 1 if guest_label == v_label else 1
                    v_label, v_seen = guest_label, now
                    if v_streak >= REQUIRED_FRAMES and guest_label not in v_notified:
                        v_notified.add(guest_label)
                        self.update(visitor=name, visitor_since=time.strftime('%H:%M:%S'))
                        self.event(f'偵測到訪客：{name}，建議開鎖（請在網頁按「解鎖」）')
                else:
                    v_streak = 0
                    if v_notified and now - v_seen >= REARM_SECONDS:
                        v_notified.clear()
                with self.guard:
                    visitor = self.state['visitor']
                if visitor and now - v_seen >= VISITOR_HOLD:
                    self.update(visitor=None, visitor_since=None)

                # 離開鏡頭 REARM_SECONDS 秒後才重新允許自動解鎖
                if len(faces) == 0:
                    absent_since = absent_since or now
                    if now - absent_since >= REARM_SECONDS:
                        armed = True
                else:
                    absent_since = None
                if accepted is not None:
                    streak = streak + 1 if candidate == accepted else 1
                    candidate = accepted
                else:
                    candidate, streak = None, 0

                with self.guard:
                    auto = self.state['auto']
                g_name, g_score = None, None
                if not auto:
                    phase = 'face'
                elif phase == 'face':
                    if armed and streak >= REQUIRED_FRAMES and now >= cooldown_until:
                        if gesture is None:
                            armed = False
                            self.door('UNLOCK', f'辨識成功 {name}')
                        else:
                            phase, target, target_name = 'gesture', candidate, name
                            deadline, last_seen, g_streak, g_last = now + GESTURE_WINDOW, now, 0, None
                            self.event(f'人臉通過 {name} → 請比出手勢：' +
                                       ' 或 '.join(GESTURE_LABEL.get(g, g) for g in ALLOWED_GESTURES))
                elif phase == 'gesture':
                    if accepted == target:
                        last_seen = now
                    g_name, g_score, pts = gesture.detect(frame)
                    ok_g = g_name in ALLOWED_GESTURES and g_score >= GESTURE_SCORE
                    if pts:
                        draw_hand(frame, pts, ok_g)
                    g_streak = g_streak + 1 if ok_g and g_name == g_last else (1 if ok_g else 0)
                    g_last = g_name if ok_g else None
                    left = deadline - now
                    hint = f'Show {" / ".join(ALLOWED_GESTURES)}  {max(0, left):.0f}s'
                    cv2.putText(frame, hint, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
                    if g_name:
                        cv2.putText(frame, f'{g_name} {g_score:.2f}', (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.8, (0, 220, 0) if ok_g else (0, 160, 255), 2)
                    if g_streak >= GESTURE_FRAMES:
                        armed, phase = False, 'face'
                        self.door('UNLOCK', f'人臉 {target_name} + 手勢 {GESTURE_LABEL.get(g_name, g_name)}')
                    elif now - last_seen > FACE_GRACE:
                        phase = 'face'
                        self.event(f'{target_name} 離開鏡頭，取消手勢驗證')
                    elif left <= 0:
                        phase, cooldown_until = 'face', now + RETRY_COOLDOWN
                        self.event(f'{target_name} 手勢逾時（{GESTURE_WINDOW:.0f} 秒），{RETRY_COOLDOWN:.0f} 秒後可重試')

                self.update(name=name, category=category,
                            distance=None if best is None else round(float(best), 1),
                            phase=phase, gesture=g_name,
                            gesture_score=None if g_score is None else round(float(g_score), 2),
                            countdown=round(max(0.0, deadline - now), 1) if phase == 'gesture' else None)
                ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with self.guard:
                        self.jpeg = buf.tobytes()
                        self.frame = frame
        except Exception as exc:
            self.update(message=f'辨識停止：{exc}')
            log(f'辨識錯誤：{exc}')


PAGE = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>智慧門鎖（人臉＋手勢）</title>
<style>body{font-family:sans-serif;background:#f2f1f6;color:#333;margin:0;padding:16px;text-align:center}
img{width:100%;max-width:640px;border-radius:12px;background:#000}
.card{max-width:640px;margin:12px auto;background:#fff;border-radius:12px;padding:12px;text-align:left}
button{font-size:18px;padding:12px 18px;margin:6px;border:0;border-radius:10px;color:#fff;background:#6a5acd}
button.lock{background:#c0392b}button.auto{background:#2e8b57}.ok{color:#2e8b57}.bad{color:#c0392b}
ul{padding-left:18px;margin:4px 0;font-size:14px}
.hint{background:#fff4d6;border-radius:8px;padding:8px;margin-bottom:8px;font-size:18px}
.visitor{background:#f3e5ff;border:2px solid #8e44ad;border-radius:8px;padding:10px;margin-bottom:8px;font-size:18px}
.visitor button{font-size:16px;padding:8px 14px;margin:6px 0 0}</style>
<h2>智慧門鎖（人臉＋手勢）</h2><img src="/video_feed">
<div id="pinbox" style="display:none;margin:8px">PIN：<input id="pin" type="password" inputmode="numeric" autocomplete="off"
style="font-size:18px;width:8em;padding:6px;border-radius:8px;border:1px solid #bbb"></div>
<div><button onclick="act('unlock')">解鎖</button><button class="lock" onclick="act('lock')">上鎖並暫停自動</button>
<button class="auto" onclick="act('auto')">恢復自動解鎖</button></div>
<div class="card" id="s">載入中</div><div class="card"><b>事件</b><ul id="ev"></ul></div>
<script>
const pinEl=()=>document.getElementById('pin');
try{const v=localStorage.getItem('door_pin');if(v)pinEl().value=v}catch(e){}
async function act(a){const pin=pinEl().value;try{localStorage.setItem('door_pin',pin)}catch(e){}
try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({action:a,pin})});const d=await r.json();alert0(d.message)}catch(e){alert0('伺服器連線失敗')}}
function alert0(m){const s=document.getElementById('s');s.dataset.msg=m;setTimeout(()=>{delete s.dataset.msg},4000);poll()}
async function poll(){try{const d=await(await fetch('/api/status')).json();const s=document.getElementById('s');
document.getElementById('pinbox').style.display=d.pin_required?'block':'none';
const v=d.visitor?`<div class="visitor"><b>訪客在門口：${d.visitor}</b>（${d.visitor_since}）<br>建議開鎖
<br><button onclick="act('unlock')">開門讓訪客進來</button></div>`:'';
const g=d.phase==='gesture'?`<div class="hint">請比出手勢：${d.allowed.join(' 或 ')}（剩 ${d.countdown} 秒）<br>目前偵測：${d.gesture??'—'} ${d.gesture_score??''}</div>`:'';
s.innerHTML=`${v}${g}辨識：<b>${d.name}</b> ${d.category} ${d.distance??''}<br>開鎖條件：${d.require_gesture?'人臉 + 手勢（'+d.allowed.join(' / ')+'）':'只需人臉'}
<br>Arduino：<span class="${d.connected?'ok':'bad'}">${d.arduino}</span>
<br>門鎖：${d.unlocked?'<span class="ok">[開鎖中]</span>':'[上鎖]'}　自動解鎖：${d.auto?'開':'關'}<br>${s.dataset.msg||d.message}`;
document.getElementById('ev').innerHTML=d.events.map(e=>`<li>${e}</li>`).join('')}catch(e){}}
setInterval(poll,1000);poll();
</script></html>'''


def create_web(system):
    from flask import Flask, Response, jsonify, request
    app = Flask(__name__)

    @app.route('/')
    def index():
        return PAGE

    @app.route('/api/status')
    def status():
        return jsonify(system.snapshot())

    @app.route('/api/action', methods=['POST'])
    def action():
        data = request.get_json(silent=True) or {}
        act = data.get('action')
        if CONTROL_PIN and not hmac.compare_digest(str(data.get('pin', '')), CONTROL_PIN):
            system.event(f'網頁 {act} 被拒：PIN 錯誤（{request.remote_addr}）')
            return jsonify(success=False, message='PIN 錯誤'), 403
        if act == 'auto':
            system.update(auto=True, message='已恢復自動解鎖')
            system.event('手動：恢復自動解鎖')
            return jsonify(success=True, message='已恢復自動解鎖')
        if act == 'lock':
            system.update(auto=False)
            ok, msg = system.door('LOCK', '手動上鎖')
        elif act == 'unlock':
            ok, msg = system.door('UNLOCK', '手動解鎖')
        else:
            return jsonify(success=False, message='無效指令'), 400
        return jsonify(success=ok, message=msg), (200 if ok else 503)

    @app.route('/video_feed')
    def video_feed():
        def frames():
            while not system.stop.is_set():
                with system.guard:
                    frame = system.jpeg
                if frame is not None:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                time.sleep(0.08)
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    return app


WINDOW_NAME = 'Smart Door Lock'
PANEL_W = 300


def compose_display(system, url, qr):
    """門口顯示畫面：左邊是相機畫面（含辨識框），右邊是 QR code 與門鎖狀態。"""
    with system.guard:
        frame = None if system.frame is None else system.frame.copy()
        st = dict(system.state)
    if frame is None:
        frame = np.zeros((FRAME_SIZE[1], FRAME_SIZE[0], 3), np.uint8)
        cv2.putText(frame, 'Starting camera...', (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
    remain = system.unlocked_until - time.monotonic()
    if remain > 0:
        color, text, sub = (60, 160, 60), 'UNLOCKED', f'auto-lock in {remain:.0f}s'
    elif st.get('visitor'):
        color, text, sub = (160, 40, 160), 'GUEST AT DOOR', f'{st["visitor"]} - unlock via web'
    elif st.get('phase') == 'gesture':
        color, text, sub = (0, 150, 230), 'SHOW GESTURE', f'{" / ".join(ALLOWED_GESTURES)}  {st.get("countdown") or 0:.0f}s'
    else:
        color, text, sub = (50, 50, 190), 'LOCKED', ('auto unlock: on' if st.get('auto') else 'auto unlock: OFF')
    if not ENABLE_QR:                                  # 沒有 QR：只在畫面底部加狀態列
        fh, fw = frame.shape[:2]
        bar = np.zeros((56, fw, 3), np.uint8)
        bar[:] = color
        cv2.putText(bar, text, (14, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.putText(bar, sub, (fw - 20 - cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)[0][0], 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        return np.vstack([frame, bar])
    h = frame.shape[0]
    panel = np.full((h, PANEL_W, 3), 255, np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    y = 16
    if qr is not None:
        q = min(PANEL_W - 30, h - 190)
        panel[y:y + q, (PANEL_W - q) // 2:(PANEL_W - q) // 2 + q] = cv2.resize(qr, (q, q), interpolation=cv2.INTER_NEAREST)
        y += q + 26
        cv2.putText(panel, 'Scan for control page', (14, y), font, 0.62, (60, 60, 60), 2)
        y += 22
    short = url.replace('http://', '')
    cv2.putText(panel, short, (14, y), font, 0.5 if len(short) < 26 else 0.42, (90, 90, 90), 1)
    y += 18
    if CONTROL_PIN:
        cv2.putText(panel, '(PIN required)', (14, y), font, 0.45, (120, 120, 120), 1)
    cv2.rectangle(panel, (10, h - 82), (PANEL_W - 10, h - 12), color, -1)
    cv2.putText(panel, text, (22, h - 46), font, 0.95, (255, 255, 255), 2)
    cv2.putText(panel, sub, (22, h - 22), font, 0.5, (255, 255, 255), 1)
    return np.hstack([frame, panel])


def display_loop(system, url):
    """在主執行緒顯示門口視窗（macOS 規定 GUI 要在主執行緒）。按 q 或 Esc 結束整個系統。"""
    qr = qr_image(url, 512) if ENABLE_QR else None
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)   # 不顯示 OpenCV 工具列
    if KIOSK:
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        cv2.resizeWindow(WINDOW_NAME, FRAME_SIZE[0] + (PANEL_W if ENABLE_QR else 0),
                         FRAME_SIZE[1] + (0 if ENABLE_QR else 56))
    while not system.stop.is_set():
        cv2.imshow(WINDOW_NAME, compose_display(system, url, qr))
        if cv2.waitKey(40) & 0xFF in (ord('q'), 27):
            break
    cv2.destroyAllWindows()


def want_window():
    if SHOW_WINDOW == '1':
        return True
    if SHOW_WINDOW == '0':
        return False
    return has_display()


def show_qr():
    url = control_url()
    print_qr(url)
    if not CONTROL_PIN:
        print('[注意] 沒有設定 CONTROL_PIN：任何掃到這個 QR code 的人都能開門。建議 CONTROL_PIN=1234 python app.py run')


def run(simulate=False):
    if not MODEL_FILE.exists() or not MAP_FILE.exists():
        raise RuntimeError('缺少 face.yml 或 id_map.txt，請先 capture 再 train')
    model = new_recognizer()
    model.read(str(MODEL_FILE))
    mapping = read_map()
    log(f'已載入 {len(mapping)} 位使用者：' + '、'.join(f'{n}({c})' for n, c in mapping.values()))
    gesture = GestureDetector() if REQUIRE_GESTURE else None
    log('開鎖條件：' + ('人臉 + 手勢 ' + ' / '.join(ALLOWED_GESTURES) if gesture else '只需人臉'))
    camera = Camera()
    arduino = FakeArduino() if simulate else Arduino()
    system = DoorSystem(arduino)
    arduino.connect()                                 # 失敗也繼續，watchdog 會重試
    system.update(message='系統已啟動')
    threads = [threading.Thread(target=system.recognition_loop, args=(camera, model, mapping, gesture), daemon=True),
               threading.Thread(target=arduino.watchdog, args=(system.stop,), daemon=True)]
    for t in threads:
        t.start()
    url = control_url()
    with socket.socket() as probe:                  # Flask 在背景執行緒，埠被占用時要先擋下來
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('0.0.0.0', WEB_PORT))
        except OSError:
            system.stop.set()
            camera.close()
            raise RuntimeError(f'埠 {WEB_PORT} 已被使用：先關掉其他正在跑的 app.py（pkill -f "app.py run"）')
    web = create_web(system)
    threading.Thread(target=lambda: web.run(host='0.0.0.0', port=WEB_PORT, threaded=True, use_reloader=False),
                     daemon=True).start()
    log(f'網頁控制台：{url}（本機也可用 http://localhost:{WEB_PORT}）；Ctrl+C 結束')
    if ENABLE_QR:
        print_qr(url)
    if CONTROL_PIN:
        log('網頁開鎖需要 PIN')
    elif ENABLE_QR:
        log('[注意] 沒有設定 CONTROL_PIN：同網路任何人（含掃到 QR code 的人）都能從網頁開門')
    try:
        if want_window():
            log(('門口顯示視窗' if ENABLE_QR else '預覽視窗') + '已開啟（在視窗按 q 或 Esc 結束整個系統）')
            display_loop(system, url)
        else:
            while not system.stop.wait(0.5):
                pass
    finally:
        system.stop.set()
        threads[0].join(timeout=3)
        arduino.send('LOCK')
        arduino.close()
        camera.close()
        if gesture is not None:
            gesture.close()
        log('已上鎖並結束')


def test_arduino():
    arduino = Arduino()
    if not arduino.connect():
        raise RuntimeError(arduino.status)
    print(arduino.send('UNLOCK'))
    print(f'等待 Arduino {UNLOCK_SECONDS} 秒後自動上鎖（應聽到第二聲喀）...')
    time.sleep(UNLOCK_SECONDS + 1)
    print(arduino.send('LOCK'))
    arduino.close()


def test_camera(seconds=10):
    """不需要 Arduino 和網頁：測相機 FPS、人臉偵測，若已訓練也顯示辨識結果，最後存一張 camera_test.jpg。"""
    detector = load_detector()
    model, mapping = None, {}
    if MODEL_FILE.exists() and MAP_FILE.exists():
        model = new_recognizer()
        model.read(str(MODEL_FILE))
        mapping = read_map()
    camera = Camera()
    frames, failures, last_print, start, frame = 0, [0], 0.0, time.monotonic(), None
    log(f'相機測試 {seconds} 秒，請站到鏡頭前' + ('' if model else '（尚未訓練，只測人臉偵測）'))
    try:
        while time.monotonic() - start < seconds:
            frame = read_frame(camera, failures)
            if frame is None:
                continue
            frames += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, 1.2, 5, minSize=MIN_FACE)
            results = []
            for x, y, w, h in faces:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                if model is not None:
                    label, dist = model.predict(normalize(gray[y:y + h, x:x + w]))
                    name = mapping.get(label, ('?', ''))[0]
                    results.append(f'{name if dist < THRESHOLD else "Unknown"}(距離 {dist:.0f})')
            now = time.monotonic()
            if now - last_print >= 1:
                last_print = now
                fps = frames / (now - start)
                print(f'  {fps:4.1f} fps | 偵測到 {len(faces)} 張臉 ' + ' '.join(results), flush=True)
    finally:
        camera.close()
    if frame is None:
        raise RuntimeError('一張畫面都沒讀到')
    out = BASE / 'camera_test.jpg'
    cv2.imwrite(str(out), frame)
    log(f'相機正常，{frame.shape[1]}x{frame.shape[0]}，最後一張存到 {out}')


def test_gesture(seconds=20):
    """不需要人臉、Arduino：即時顯示手勢與信心值。有螢幕時開預覽視窗畫出手部骨架（按 q 提早結束）。"""
    gesture = GestureDetector()
    camera = Camera()
    show = has_display()
    failures, start, last_print, frame, frames = [0], time.monotonic(), 0.0, None, 0
    log(f'手勢測試 {seconds} 秒：對鏡頭比出手勢（合格手勢：{" / ".join(ALLOWED_GESTURES)}）'
        + ('，按 q 提早結束' if show else ''))
    try:
        while time.monotonic() - start < seconds:
            frame = read_frame(camera, failures)
            if frame is None:
                continue
            frames += 1
            name, score, pts = gesture.detect(frame)
            ok = name in ALLOWED_GESTURES and score >= GESTURE_SCORE
            if pts:
                draw_hand(frame, pts, ok)
            now = time.monotonic()
            fps = frames / max(now - start, 1e-6)
            if now - last_print >= 0.5:
                last_print = now
                shown = f'{GESTURE_LABEL.get(name, name)} ({score:.2f})' if name else ('有手，無法判斷' if pts else '沒偵測到手')
                print(f'  {fps:4.1f} fps | {shown}' + ('  <- 合格' if ok else ''), flush=True)
            if show:
                fh, fw = frame.shape[:2]
                if name:
                    draw_label(frame, f'{name}  {score:.2f}', (12, 34), 0.9,
                               (255, 255, 255), (0, 150, 0) if ok else (0, 110, 200))
                    draw_bar(frame, 12, 48, 200, 12, score, (0, 200, 0) if ok else (0, 160, 255))
                else:
                    draw_label(frame, 'Hand found, unknown gesture' if pts else 'No hand', (12, 34), 0.8,
                               (255, 255, 255), (80, 80, 80))
                draw_label(frame, f'Accept: {" / ".join(ALLOWED_GESTURES)}   {fps:.0f} fps', (12, fh - 16), 0.55)
                draw_label(frame, f'{max(0, seconds - (now - start)):.0f}s', (fw - 60, 34), 0.7)
                cv2.imshow('Gesture test (q = quit)', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    finally:
        camera.close()
        gesture.close()
        if show:
            cv2.destroyAllWindows()
    if frame is not None:
        cv2.imwrite(str(BASE / 'gesture_test.jpg'), frame)
        log(f'最後一張存到 {BASE / "gesture_test.jpg"}')


# ==========================================
# 主程式
# ==========================================
def menu():
    while True:
        print('\n1. 採集人臉\n2. 訓練模型\n3. 啟動門鎖系統\n4. 測試 Arduino\n5. 離開'
              '\n6. 測試相機\n7. 啟動門鎖系統（模擬 Arduino）\n8. 測試手勢')
        choice = input('選擇：').strip()
        try:
            if choice == '1':
                capture(input('使用者 ID（英文/數字）：').strip())
            elif choice == '2':
                train()
            elif choice == '3':
                run()
                return
            elif choice == '4':
                test_arduino()
            elif choice == '5':
                return
            elif choice == '6':
                test_camera()
            elif choice == '7':
                run(simulate=True)
                return
            elif choice == '8':
                test_gesture()
        except (RuntimeError, ValueError, OSError, cv2.error) as exc:
            print('錯誤：', exc)


def main(argv):
    commands = {'train': train, 'run': run, 'test-arduino': test_arduino, 'test-camera': test_camera,
                'test-gesture': test_gesture}
    if ENABLE_QR:
        commands['qr'] = show_qr
    try:
        if not argv:
            menu()
        elif argv[0] == 'capture' and len(argv) == 2:
            capture(argv[1])
        elif argv == ['run', '--sim'] or (argv == ['run'] and os.environ.get('SIM_ARDUINO') == '1'):
            run(simulate=True)
        elif argv[0] in commands and len(argv) == 1:
            commands[argv[0]]()
        else:
            print(__doc__)
            return 2
    except (RuntimeError, ValueError, OSError, cv2.error) as exc:
        print('錯誤：', exc)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
