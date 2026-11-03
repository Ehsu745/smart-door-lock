"""指令列入口。

用法（在專案資料夾，先 source .venv/bin/activate）：
  python app.py                 互動選單
  python app.py capture yang    採集 yang 的人臉 30 張
  python app.py train           以 faces/ 訓練 LBPH 模型
  python app.py run             啟動辨識 + 網頁控制台 + Arduino 控制
  python app.py run --sim       同上，但不接 Arduino，用文字模擬開鎖/上鎖
  python app.py test-camera     只測相機：FPS、人臉偵測、辨識距離，存 camera_test.jpg
  python app.py test-arduino    只測試 Arduino：上鎖 → 解鎖 → 等待自動上鎖
  python app.py test-gesture    只測手勢：即時顯示手勢與信心值，存 gesture_test.jpg
  python app.py qr              在終端機印出控制頁網址的 QR code
（也可以用 python -m smartlock <指令>）
"""
from __future__ import annotations

import dataclasses
import logging
import signal
import socket
import sys
import threading
import time

import cv2

from .arduino import Arduino, FakeArduino
from .camera import Camera, FrameReader
from .config import GESTURE_LABEL, Config
from .display import display_loop, draw_bar, draw_label, has_display, want_window
from .door import DoorSystem
from .events import EventBus
from .face import FaceRecognizer, capture, load_detector, train
from .gesture import GestureDetector, draw_hand
from .qr import control_url, print_qr
from .web import create_web

log = logging.getLogger('smartlock')


def setup_logging(level=logging.INFO):
    logging.basicConfig(level=level, format='%(asctime)s %(message)s', datefmt='%H:%M:%S', stream=sys.stdout)


# ==========================================
# run：正式啟動
# ==========================================
def check_port_free(port: int):
    with socket.socket() as probe:                  # Flask 在背景執行緒，埠被占用時要先擋下來
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('0.0.0.0', port))
        except OSError:
            raise RuntimeError(f'埠 {port} 已被使用：先關掉其他正在跑的程式（pkill -f "app.py run"）')


def run(cfg: Config):
    recognizer = FaceRecognizer.load(cfg)
    log.info('已載入 %d 位使用者：%s', len(recognizer.mapping),
             '、'.join(f'{n}({c})' for n, c in recognizer.mapping.values()))
    gesture = GestureDetector(cfg) if cfg.require_gesture else None
    log.info('開鎖條件：%s', '人臉 + 手勢 ' + ' / '.join(cfg.allowed_gestures) if gesture else '只需人臉')
    check_port_free(cfg.web_port)
    camera = Camera(cfg)
    arduino = FakeArduino(cfg) if cfg.simulate else Arduino(cfg)
    system = DoorSystem(cfg, arduino, recognizer, gesture, EventBus())
    arduino.connect()                                 # 失敗也繼續，watchdog 會重試
    system.update(message='系統已啟動')

    # systemctl stop 送的是 SIGTERM：轉成正常結束，才會執行下面的上鎖與清理
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: system.stop.set())

    worker = threading.Thread(target=system.run_loop, args=(camera,), daemon=True, name='recognition')
    worker.start()
    threading.Thread(target=arduino.watchdog, args=(system.stop,), daemon=True, name='arduino-watchdog').start()
    web = create_web(system)
    threading.Thread(target=lambda: web.run(host='0.0.0.0', port=cfg.web_port, threaded=True, use_reloader=False),
                     daemon=True, name='web').start()

    url = control_url(cfg)
    log.info('網頁控制台：%s（本機也可用 http://localhost:%d）；Ctrl+C 結束', url, cfg.web_port)
    if cfg.enable_qr:
        print_qr(url)
    if cfg.control_pin:
        log.info('網頁開鎖需要 PIN')
    elif cfg.enable_qr:
        log.info('[注意] 沒有設定 CONTROL_PIN：同網路任何人（含掃到 QR code 的人）都能從網頁開門')
    try:
        if want_window(cfg):
            log.info('%s已開啟（在視窗按 q 或 Esc 結束整個系統）', '門口顯示視窗' if cfg.enable_qr else '預覽視窗')
            display_loop(system, cfg, url)
        else:
            while not system.stop.wait(0.5):
                pass
    finally:
        system.stop.set()
        worker.join(timeout=3)
        arduino.send('LOCK')
        arduino.close()
        camera.close()
        if gesture is not None:
            gesture.close()
        log.info('已上鎖並結束')


# ==========================================
# 測試指令
# ==========================================
def test_arduino(cfg: Config):
    arduino = Arduino(cfg)
    if not arduino.connect():
        raise RuntimeError(arduino.status)
    print(arduino.send('UNLOCK'))
    print(f'等待 Arduino {cfg.unlock_seconds:.0f} 秒後自動上鎖（應聽到第二聲喀）...')
    time.sleep(cfg.unlock_seconds + 1)
    print(arduino.send('LOCK'))
    arduino.close()


def test_camera(cfg: Config, seconds: float = 10):
    """測相機 FPS、人臉偵測；已訓練時也顯示辨識結果，最後存一張 camera_test.jpg。"""
    recognizer = FaceRecognizer.load(cfg) if cfg.model_file.exists() and cfg.map_file.exists() else None
    detector = None if recognizer else load_detector(cfg)
    camera = Camera(cfg)
    reader = FrameReader(camera)
    frames, last_print, start, frame = 0, 0.0, time.monotonic(), None
    log.info('相機測試 %.0f 秒，請站到鏡頭前%s', seconds, '' if recognizer else '（尚未訓練，只測人臉偵測）')
    try:
        while time.monotonic() - start < seconds:
            frame = reader.read()
            if frame is None:
                continue
            frames += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if recognizer:
                results = recognizer.identify(gray)
                boxes = [r.box for r in results]
                texts = [f'{r.person if r.distance < cfg.threshold else "Unknown"}(距離 {r.distance:.0f})' for r in results]
            else:
                boxes = list(detector.detectMultiScale(gray, 1.2, 5, minSize=cfg.min_face))
                texts = []
            for x, y, w, h in boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            now = time.monotonic()
            if now - last_print >= 1:
                last_print = now
                print(f'  {frames / (now - start):4.1f} fps | 偵測到 {len(boxes)} 張臉 ' + ' '.join(texts), flush=True)
    finally:
        camera.close()
    if frame is None:
        raise RuntimeError('一張畫面都沒讀到')
    out = cfg.base / 'camera_test.jpg'
    cv2.imwrite(str(out), frame)
    log.info('相機正常，%dx%d，最後一張存到 %s', frame.shape[1], frame.shape[0], out)


def test_gesture(cfg: Config, seconds: float = 20):
    """即時顯示手勢與信心值。有螢幕時開預覽視窗畫出手部骨架（按 q 提早結束）。"""
    gesture = GestureDetector(cfg)
    camera = Camera(cfg)
    reader = FrameReader(camera)
    show = has_display()
    start, last_print, frame, frames = time.monotonic(), 0.0, None, 0
    accept = ' / '.join(cfg.allowed_gestures)
    log.info('手勢測試 %.0f 秒：對鏡頭比出手勢（合格手勢：%s）%s', seconds, accept, '，按 q 提早結束' if show else '')
    try:
        while time.monotonic() - start < seconds:
            frame = reader.read()
            if frame is None:
                continue
            frames += 1
            name, score, pts = gesture.detect(frame)
            ok = name in cfg.allowed_gestures and score >= cfg.gesture_score
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
                draw_label(frame, f'Accept: {accept}   {fps:.0f} fps', (12, fh - 16), 0.55)
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
        out = cfg.base / 'gesture_test.jpg'
        cv2.imwrite(str(out), frame)
        log.info('最後一張存到 %s', out)


def show_qr(cfg: Config):
    print_qr(control_url(cfg))
    if not cfg.control_pin:
        print('[注意] 沒有設定 CONTROL_PIN：任何掃到這個 QR code 的人都能開門。建議 CONTROL_PIN=1234 python app.py run')


# ==========================================
# 主程式
# ==========================================
def menu(cfg: Config):
    while True:
        print('\n1. 採集人臉\n2. 訓練模型\n3. 啟動門鎖系統\n4. 測試 Arduino\n5. 離開'
              '\n6. 測試相機\n7. 啟動門鎖系統（模擬 Arduino）\n8. 測試手勢')
        choice = input('選擇：').strip()
        try:
            if choice == '1':
                capture(cfg, input('使用者 ID（英文/數字）：').strip())
            elif choice == '2':
                train(cfg)
            elif choice == '3':
                run(cfg)
                return
            elif choice == '4':
                test_arduino(cfg)
            elif choice == '5':
                return
            elif choice == '6':
                test_camera(cfg)
            elif choice == '7':
                run(dataclasses.replace(cfg, simulate=True))
                return
            elif choice == '8':
                test_gesture(cfg)
        except (RuntimeError, ValueError, OSError, cv2.error) as exc:
            print('錯誤：', exc)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    try:
        cfg = Config.from_env()
        commands = {'train': train, 'run': run, 'test-arduino': test_arduino,
                    'test-camera': test_camera, 'test-gesture': test_gesture}
        if cfg.enable_qr:
            commands['qr'] = show_qr
        if not argv:
            menu(cfg)
        elif argv[0] == 'capture' and len(argv) == 2:
            capture(cfg, argv[1])
        elif argv == ['run', '--sim']:
            run(dataclasses.replace(cfg, simulate=True))
        elif argv[0] in commands and len(argv) == 1:
            commands[argv[0]](cfg)
        else:
            print(__doc__)
            return 2
    except (RuntimeError, ValueError, OSError, cv2.error) as exc:
        print('錯誤：', exc)
        return 1
    except KeyboardInterrupt:
        pass
    return 0
