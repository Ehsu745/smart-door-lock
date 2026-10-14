"""樹莓派人臉辨識門鎖雛形：Pi 5 + Pi 相機（Picamera2）+ USB 連接 Arduino 控制繼電器。

用法（在 app.py 所在資料夾）：
  python3 app.py                 互動選單
  python3 app.py capture yang    採集 yang 的人臉 30 張
  python3 app.py train           以 faces/ 訓練 LBPH 模型
  python3 app.py run             啟動辨識 + 網頁控制台 + Arduino 控制
  python3 app.py test-arduino    只測試 Arduino：上鎖 → 解鎖 → 等待自動上鎖

資料沿用舊版格式：faces/<ID>_*.jpg、face.yml、id_map.txt（編號:名字:master|guest）。
"""
import glob
import os
import re
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
BAUD = 9600                     # 需與 door_lock.ino 的 Serial.begin 一致
WEB_PORT = int(os.environ.get('WEB_PORT', 5050))


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
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


# ==========================================
# 相機：優先 Pi 相機（Picamera2），沒有時改用 USB webcam
# ==========================================
class Camera:
    def __init__(self):
        self.picam = None
        self.cap = None
        try:
            from picamera2 import Picamera2
            if Picamera2.global_camera_info():
                self.picam = Picamera2()
                config = self.picam.create_video_configuration(
                    main={'size': FRAME_SIZE, 'format': 'RGB888'})   # RGB888 的記憶體順序即 BGR
                self.picam.configure(config)
                self.picam.start()
                time.sleep(1)                                         # 自動曝光暖機
                log('相機：Pi 相機（Picamera2）')
                return
            log('Picamera2 沒有偵測到 Pi 相機，改試 USB webcam')
        except ImportError:
            log('沒有 picamera2 套件，改試 USB webcam')
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            raise RuntimeError('鏡頭無法開啟：先用 rpicam-hello --list-cameras 確認相機，'
                               '並關閉其他使用相機的程式')
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_SIZE[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_SIZE[1])
        log('相機：USB webcam')

    def read(self):
        if self.picam is not None:
            return self.picam.capture_array()
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
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
    saved, last_save, failures = 0, 0.0, [0]
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
                    print(f'\r已採集 {saved}/{count}', end='', flush=True)
            if show:
                for x, y, w, h in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0) if len(faces) == 1 else (0, 0, 255), 2)
                cv2.imshow('Capture (q = quit)', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    finally:
        camera.close()
        if show:
            cv2.destroyAllWindows()
    print()
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
# 3. Arduino 控制（USB serial，協定：送 UNLOCK/LOCK，回 UNLOCKED/LOCKED）
# ==========================================
class Arduino:
    REPLY = {'UNLOCK': 'UNLOCKED', 'LOCK': 'LOCKED'}

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
        for pattern in ('/dev/serial/by-id/*', '/dev/ttyACM*', '/dev/ttyUSB*'):
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

    def _exchange(self, command, timeout=1.5):
        self.ser.reset_input_buffer()            # 丟掉 Arduino 自動上鎖時主動送出的 LOCKED
        self.ser.write((command + '\n').encode('ascii'))
        self.ser.flush()
        expected = self.REPLY[command]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.ser.readline().decode('ascii', errors='replace').strip()
            if line == expected:
                return True
            if line.startswith('ERROR'):
                log(f'Arduino 回報：{line}')
        return False

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
                if self._exchange('LOCK'):       # 用「上鎖」當握手：安全，且新舊韌體都支援
                    self.port = port
                    self.status = f'已連線 {port}'
                    log(f'Arduino {self.status}')
                    return True
                self.status = f'{port} 有開啟但 Arduino 沒回應：確認已上傳 door_lock.ino、baud={self.baud}'
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
                if self._exchange(command):
                    return True, f'Arduino 已回覆 {self.REPLY[command]}'
                return False, f'已送出 {command}，但 Arduino 未確認'
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


# ==========================================
# 4. 辨識 + 網頁 + 門鎖
# ==========================================
class DoorSystem:
    def __init__(self, arduino):
        self.arduino = arduino
        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.jpeg = None
        self.events = deque(maxlen=12)
        self.state = {'name': '無人臉', 'category': '', 'distance': None,
                      'auto': True, 'message': '啟動中'}

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
        return data

    def door(self, command, reason):
        ok, msg = self.arduino.send(command)
        self.update(message=msg)
        self.event(f'{reason} → {command} {"成功" if ok else "失敗：" + msg}')
        return ok, msg

    def recognition_loop(self, camera, model, mapping):
        candidate, streak, armed, absent_since, failures = None, 0, True, None, [0]
        last_label = None
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
                for x, y, w, h in faces:
                    label, distance = model.predict(normalize(gray[y:y + h, x:x + w]))
                    person, kind = mapping.get(label, ('Unknown', 'Stranger'))
                    valid = distance < THRESHOLD and kind in ('master', 'guest')
                    color = (0, 255, 0) if valid else (0, 0, 255)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    cv2.putText(frame, f'{person if valid else "Unknown"} {distance:.0f}',
                                (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    if len(faces) == 1:
                        name, category, best = (person, kind, distance) if valid else ('Unknown', 'Stranger', distance)
                        accepted = label if valid else None
                if len(faces) > 1:
                    name = '多人入鏡，暫不自動解鎖'
                self.update(name=name, category=category,
                            distance=None if best is None else round(float(best), 1))
                if name != last_label and name not in ('無人臉',):
                    self.event(f'看到 {name} {category}'.strip())
                last_label = name

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
                if auto and armed and streak >= REQUIRED_FRAMES:
                    armed = False
                    self.door('UNLOCK', f'辨識成功 {name}')

                ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with self.guard:
                        self.jpeg = buf.tobytes()
        except Exception as exc:
            self.update(message=f'辨識停止：{exc}')
            log(f'辨識錯誤：{exc}')


PAGE = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>智慧門鎖</title>
<style>body{font-family:sans-serif;background:#f2f1f6;color:#333;margin:0;padding:16px;text-align:center}
img{width:100%;max-width:640px;border-radius:12px;background:#000}
.card{max-width:640px;margin:12px auto;background:#fff;border-radius:12px;padding:12px;text-align:left}
button{font-size:18px;padding:12px 18px;margin:6px;border:0;border-radius:10px;color:#fff;background:#6a5acd}
button.lock{background:#c0392b}button.auto{background:#2e8b57}.ok{color:#2e8b57}.bad{color:#c0392b}
ul{padding-left:18px;margin:4px 0;font-size:14px}</style>
<h2>智慧門鎖</h2><img src="/video_feed">
<div><button onclick="act('unlock')">解鎖</button><button class="lock" onclick="act('lock')">上鎖並暫停自動</button>
<button class="auto" onclick="act('auto')">恢復自動解鎖</button></div>
<div class="card" id="s">載入中</div><div class="card"><b>事件</b><ul id="ev"></ul></div>
<script>
async function act(a){try{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({action:a})});const d=await r.json();alert0(d.message)}catch(e){alert0('伺服器連線失敗')}}
function alert0(m){const s=document.getElementById('s');s.dataset.msg=m;setTimeout(()=>{delete s.dataset.msg},4000);poll()}
async function poll(){try{const d=await(await fetch('/api/status')).json();const s=document.getElementById('s');
s.innerHTML=`辨識：<b>${d.name}</b> ${d.category} ${d.distance??''}<br>Arduino：<span class="${d.connected?'ok':'bad'}">${d.arduino}</span>
<br>自動解鎖：${d.auto?'開':'關'}<br>${s.dataset.msg||d.message}`;
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


def run():
    if not MODEL_FILE.exists() or not MAP_FILE.exists():
        raise RuntimeError('缺少 face.yml 或 id_map.txt，請先 capture 再 train')
    model = new_recognizer()
    model.read(str(MODEL_FILE))
    mapping = read_map()
    log(f'已載入 {len(mapping)} 位使用者：' + '、'.join(f'{n}({c})' for n, c in mapping.values()))
    camera = Camera()
    arduino = Arduino()
    system = DoorSystem(arduino)
    arduino.connect()                                 # 失敗也繼續，watchdog 會重試
    system.update(message='系統已啟動')
    threads = [threading.Thread(target=system.recognition_loop, args=(camera, model, mapping), daemon=True),
               threading.Thread(target=arduino.watchdog, args=(system.stop,), daemon=True)]
    for t in threads:
        t.start()
    log(f'網頁控制台：http://localhost:{WEB_PORT}（同網路其他裝置用 http://<Pi 的 IP>:{WEB_PORT}）；Ctrl+C 結束')
    try:
        create_web(system).run(host='0.0.0.0', port=WEB_PORT, threaded=True, use_reloader=False)
    finally:
        system.stop.set()
        threads[0].join(timeout=3)
        arduino.send('LOCK')
        arduino.close()
        camera.close()
        log('已上鎖並結束')


def test_arduino():
    arduino = Arduino()
    if not arduino.connect():
        raise RuntimeError(arduino.status)
    print(arduino.send('UNLOCK'))
    print('等待 Arduino 5 秒後自動上鎖...')
    time.sleep(6)
    print(arduino.send('LOCK'))
    arduino.close()


# ==========================================
# 主程式
# ==========================================
def menu():
    while True:
        print('\n1. 採集人臉\n2. 訓練模型\n3. 啟動門鎖系統\n4. 測試 Arduino\n5. 離開')
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
        except (RuntimeError, ValueError, OSError, cv2.error) as exc:
            print('錯誤：', exc)


def main(argv):
    commands = {'train': train, 'run': run, 'test-arduino': test_arduino}
    try:
        if not argv:
            menu()
        elif argv[0] == 'capture' and len(argv) == 2:
            capture(argv[1])
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
