"""指令列入口。

用法（在專案資料夾，先 source .venv/bin/activate）：
  python app.py                 互動選單
  python app.py capture yang    採集 yang 的人臉 30 張（capture yang 8：只拍 8 張，適合每次換地方補拍）
  python app.py train           以 faces/ 的照片登錄人臉特徵（SFace）
  python app.py run             啟動辨識 + 網頁控制台 + Arduino 控制
  python app.py run --sim       同上，但不接 Arduino，用文字模擬開鎖/上鎖
  python app.py test-camera     只測相機：FPS、人臉偵測、辨識距離，存 camera_test.jpg
  python app.py check-face      站在鏡頭前 20 秒，統計本人距離與通過率，判斷要不要補拍（check-face 30：量 30 秒）
  python app.py test-arduino    只測試 Arduino：上鎖 → 解鎖 → 等待自動上鎖
  python app.py test-gesture    只測手勢：即時顯示手勢與信心值，存 gesture_test.jpg
  python app.py qr              在終端機印出控制頁網址的 QR code
  python app.py records         哨兵錄影清單、容量與最近的事件紀錄
  python app.py cleanup         立即執行容量管理（刪除過期 / 超量的錄影）
  python app.py set-pin         設定或重設網頁 PIN（忘記 PIN 時用）
  python app.py set-gesture 名字 手勢   綁定開鎖手勢（手勢填 none 取消；不加參數列出目前設定）
  python app.py test-line       查 LINE 額度，確認後送一則測試訊息
  python app.py test-audio      對講機：找 USB 音效卡、錄 5 秒、左右聲道各播一次、看有沒有欠壓

設定會自動從專案資料夾的 smartlock.env 讀取；指令前面加的環境變數優先。
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
from .face import FaceRecognizer, capture, make_engine, train
from .gesture import GestureDetector, draw_hand
from .intercom import make_intercom
from .bindings import GESTURES, GestureBindings
from .learning import AttemptLog, Hardening
from .config import PROJECT_ROOT
from .qr import control_url, print_qr
from .line import LineClient, LineError, LineNotifier
from .security import ENV_FILE, PinStore, load_env_file
from .sentinel import Sentinel
from .storage import Storage
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


def serve_web(web, port, stop):
    """網頁伺服器：卡住的連線 20 秒後自動斷開；萬一伺服器本身出錯就記錄下來並重新啟動，不需要重開整個程式。"""
    from werkzeug.serving import WSGIRequestHandler
    from .web.server import STREAM_SEND_TIMEOUT

    class Handler(WSGIRequestHandler):
        timeout = STREAM_SEND_TIMEOUT              # 手機休眠或離線時，送不出去的串流不會永遠佔住執行緒

    while not stop.is_set():
        try:
            web.run(host='0.0.0.0', port=port, threaded=True, use_reloader=False, request_handler=Handler)
        except Exception:
            log.exception('網頁伺服器錯誤，3 秒後重新啟動')
        if stop.wait(3):
            break


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
    system.pins = PinStore(cfg.pin_file, cfg.control_pin)
    system.bindings = GestureBindings(cfg.gestures_file)
    system.info = {'camera': camera.name, 'gesture': getattr(gesture, 'mode', '') if gesture else '未使用'}
    storage = Storage(cfg)
    system.bus.subscribe('*', storage.log_event)      # 所有事件寫入 SQLite（網頁「事件紀錄」）
    AttemptLog(cfg, storage, system.bus)              # 每次人臉通過記一筆，給「這不是 某某」回報用
    hardening = Hardening(cfg, storage, recognizer, system.bus)
    removed = storage.enforce()
    if removed:
        log.info('啟動時清掉 %d 段過期 / 超量的錄影', removed)
    sentinel = None
    if cfg.sentinel:
        sentinel = Sentinel(cfg, storage, system.bus)
        system.frame_hooks.append(sentinel.observe)
        system.providers['sentinel'] = lambda: sentinel.status(system.clock())
        u = storage.usage()
        log.info('哨兵：陌生人 ≥ %.0f 秒自動錄影；已有 %d 段 %.0f MB / 上限 %.0f MB、保留 %.0f 天；SD 卡剩 %.1f GB',
                 cfg.sentinel_trigger, u.count, u.used_mb, cfg.record_max_mb, cfg.record_max_days,
                 u.disk_free_mb / 1024)
    arduino.connect()                                 # 失敗也繼續，watchdog 會重試
    system.update(message='系統已啟動')

    # systemctl stop 送的是 SIGTERM：轉成正常結束，才會執行下面的上鎖與清理
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: system.stop.set())

    worker = threading.Thread(target=system.run_loop, args=(camera,), daemon=True, name='recognition')
    worker.start()
    threading.Thread(target=arduino.watchdog, args=(system.stop,), daemon=True, name='arduino-watchdog').start()
    line = LineClient(cfg.line_token) if cfg.line_token else None
    notifier = None
    if line is not None and cfg.line_notify:
        notifier = LineNotifier(line, system.bus, user_id=cfg.line_user_id, cooldown=cfg.line_cooldown)
        log.info('LINE 通知：%s（訪客、陌生人錄影、開鎖失敗）', '只推給 LINE_USER_ID' if cfg.line_user_id else '廣播給所有好友')
    intercom = make_intercom(cfg, system.bus)
    web = create_web(system, storage, line, notifier, hardening, intercom)
    threading.Thread(target=serve_web, args=(web, cfg.web_port, system.stop), daemon=True, name='web').start()

    url = control_url(cfg)
    log.info('網頁控制台：%s（本機也可用 http://localhost:%d）；Ctrl+C 結束', url, cfg.web_port)
    if cfg.enable_qr:
        print_qr(url)
    if system.pins.required:
        log.info('網頁開鎖需要 PIN（來源：%s）', 'pin.json' if system.pins.source == 'file' else 'CONTROL_PIN')
    elif cfg.enable_qr:
        log.info('[注意] 沒有設定 PIN：同網路任何人（含掃到 QR code 的人）都能從網頁開門。'
                 '用 python app.py set-pin 設定')
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
        if sentinel is not None:
            sentinel.close(system.clock())              # 把錄到一半的影片收尾
        if notifier is not None:
            notifier.close()                            # 把排隊中的通知送完
        storage.close()
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
    recognizer = FaceRecognizer.load(cfg) if cfg.map_file.exists() and cfg.face_dir.exists() else None
    engine = None if recognizer else make_engine(cfg)
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
                texts = [f'{r.person if r.known else "Unknown"}(距離 {r.distance:.0f})' for r in results]
            else:
                boxes = [tuple(int(v) for v in f[:4]) for f in engine.detect(frame)]
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


def parse_count(text: str) -> int:
    """capture 的張數：1～200。"""
    if not text.isdigit() or not 1 <= int(text) <= 200:
        raise ValueError(f'張數要是 1～200 的整數，收到 {text!r}')
    return int(text)


def parse_seconds(text: str) -> float:
    """check-face 的秒數：5～300。"""
    try:
        sec = float(text)
    except ValueError:
        sec = 0
    if not 5 <= sec <= 300:
        raise ValueError(f'秒數要在 5～300 之間，收到 {text!r}')
    return sec


CHECK_GOOD = 40.0     # check-face：距離中位數低於這個值算穩定（距門檻 50 留約 10 的餘裕）


def face_check_summary(samples, threshold: float, required: int) -> dict:
    """check-face 的統計。samples 每幀一筆：(人名, 距離, 是否認得)，沒找到臉是 None。"""
    seen = [s for s in samples if s is not None]
    dist = sorted(d for _, d, _ in seen)
    streak = best = runs = 0
    for s in samples:
        streak = streak + 1 if s is not None and s[2] else 0
        best = max(best, streak)
        runs += streak == required
    out = {'frames': len(samples), 'faces': len(seen), 'known': sum(1 for s in seen if s[2]),
           'longest': best, 'unlock_runs': runs, 'person': None, 'verdict': 'noface'}
    if not dist:
        return out
    pct = lambda q: dist[min(len(dist) - 1, int(q * len(dist)))]
    names = [n for n, _, k in seen if k]          # 只算認得的幀：沒過門檻時的「最接近成員」不能當成這個人
    out.update(person=max(set(names), key=names.count) if names else None, min=dist[0], p10=pct(0.1), median=pct(0.5),
               p90=pct(0.9), max=dist[-1],
               rate={th: sum(d < th for d in dist) / len(dist) for th in sorted({40, 45, threshold, 55})})
    out['verdict'] = ('good' if out['median'] < CHECK_GOOD and runs else
                      'weak' if runs else 'fail')
    return out


def check_face(cfg: Config, seconds: float = 20, camera=None):
    """站在鏡頭前量 seconds 秒：本人距離分布、通過率、達到連續通過幾次。不存任何照片。"""
    recognizer = FaceRecognizer.load(cfg)
    camera = camera or Camera(cfg)
    reader = FrameReader(camera)
    log.info('人臉檢查 %.0f 秒：看著鏡頭，慢慢左右轉頭、上下點頭（只有一個人在畫面裡）', seconds)
    samples, start, last_print = [], time.monotonic(), 0.0
    try:
        while time.monotonic() - start < seconds:
            frame = reader.read()
            if frame is None:
                continue
            results = recognizer.identify(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            if not results:
                samples.append(None)
            else:
                top = max(results, key=lambda r: r.box[2] * r.box[3])     # 最大的臉 = 站最近的人
                known = top.is_master or top.is_guest
                samples.append((top.person if known else 'Unknown', top.distance, known))
            now = time.monotonic()
            if now - last_print >= 1 and samples[-1] is not None:
                last_print = now
                print(f'  {samples[-1][0]} 距離 {samples[-1][1]:.0f}', flush=True)
    finally:
        camera.close()
    elapsed = max(time.monotonic() - start, 1e-6)
    s = face_check_summary(samples, cfg.threshold, cfg.required_frames)
    print(f'\n{s["frames"]} 幀（{s["frames"] / elapsed:.1f} fps），找到臉 {s["faces"]} 幀')
    if s['verdict'] == 'noface':
        print('沒找到臉：確認鏡頭對著臉、不要逆光')
        return s
    print(f'認成：{s["person"]}' if s['person'] else '沒有被認成任何成員')
    print(f'距離 最小 {s["min"]:.1f} / p10 {s["p10"]:.1f} / 中位 {s["median"]:.1f} / p90 {s["p90"]:.1f}'
          f' / 最大 {s["max"]:.1f}（門檻 {cfg.threshold:.0f}，越小越像）')
    print(f'認得 {s["known"]}/{s["faces"]} 幀；最長連續 {s["longest"]} 幀；'
          f'達到連續 {cfg.required_frames} 幀 {s["unlock_runs"]} 次')
    print('  ' + '  '.join(f'門檻 {th:g}: {r * 100:.0f}%' for th, r in s['rate'].items()))
    name = s['person'] or '你的名字'           # 認不出來時不猜名字，免得把別人的臉補拍進成員相簿
    print({'good': f'結論：穩定（中位數 < {CHECK_GOOD:.0f}），可以直接用',
           'weak': f'結論：認得出來但不穩，建議在門口的 Pi 上補拍：python app.py capture {name} 8 → train',
           'fail': f'結論：幾乎認不出來，在門口的 Pi 上補拍：python app.py capture {name} 8 → train'}[s['verdict']])
    print('（不要為了通過去調高 FACE_THRESHOLD：陌生人也會跟著變容易通過）')
    return s


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
    if not PinStore(cfg.pin_file, cfg.control_pin).required:
        print('[注意] 沒有設定 PIN：任何掃到這個 QR code 的人都能開門。用 python app.py set-pin 設定')


# ==========================================
# 主程式
# ==========================================
def show_records(cfg: Config):
    from datetime import datetime
    storage = Storage(cfg)
    u = storage.usage()
    print(f'錄影：{u.count} 段，{u.used_mb:.0f} MB / 上限 {u.limit_mb:.0f} MB，保留 {u.keep_days:.0f} 天'
          f'｜SD 卡剩 {u.disk_free_mb / 1024:.1f} GB / {u.disk_total_mb / 1024:.1f} GB')
    print(f'位置：{storage.root}')
    for r in storage.list_records(15):
        when = datetime.fromtimestamp(r['started']).strftime('%m/%d %H:%M:%S')
        dist = '—' if r['min_distance'] is None else f'{r["min_distance"]:.0f}'
        print(f'  #{r["id"]:<4} {when}  {r["duration"]:>5.0f} 秒  {r["size_bytes"] / 1048576:>5.1f} MB  '
              f'距離 {dist:>4}  {r["status"]}  {r["video"]}')
    print('最近事件：')
    for e in storage.list_events(15):
        print(f'  {datetime.fromtimestamp(e["time"]).strftime("%m/%d %H:%M:%S")}  {e["text"]}')
    storage.close()


def cleanup(cfg: Config):
    storage = Storage(cfg)
    print(f'已刪除 {storage.enforce()} 段錄影')
    show_records(cfg)


def set_pin(cfg: Config):
    import getpass
    store = PinStore(cfg.pin_file, cfg.control_pin)
    print(f'目前：{"已設定 PIN" if store.required else "沒有 PIN"}（來源：{store.source}）')
    new = getpass.getpass('新的 PIN（4–8 位數字）：')
    if getpass.getpass('再輸入一次：') != new:
        raise ValueError('兩次輸入不一樣，PIN 沒有變更')
    store.set(new)
    print(f'PIN 已更新，存在 {cfg.pin_file}（只存雜湊）。正在執行的門鎖系統會自動套用，用舊 PIN 登入的手機要重新輸入。')


def test_line(cfg: Config):
    """查 LINE 額度，確認後送一則測試訊息。"""
    if not cfg.line_token:
        raise ValueError('smartlock.env 還沒有 LINE_CHANNEL_TOKEN')
    client = LineClient(cfg.line_token)
    q = client.quota(refresh=True)
    if q.get('error'):
        raise ValueError(q['error'])
    left = '不限' if q.get('limit') is None else f'{q["left"]} / {q["limit"]}'
    target = '只推給 LINE_USER_ID' if cfg.line_user_id else '廣播給所有好友（每位好友各算一則）'
    print(f'token 有效。本月剩餘額度：{left}，已用 {q.get("used")}；發送方式：{target}')
    if input('送一則測試訊息？(y/N) ').strip().lower() != 'y':
        return
    try:
        client.send('【智慧門鎖】測試訊息：LINE 通知設定成功。', cfg.line_user_id)
    except LineError as exc:
        raise ValueError(f'發送失敗：{exc}') from exc
    print('已送出，請看 LINE。')


def test_audio(cfg: Config, seconds: int = 5):
    """對講機硬體檢查（Pi 上）：錄音 → 左聲道（門外）播 → 右聲道（室內）播。"""
    import subprocess
    import tempfile
    import wave

    import numpy as np

    from .intercom import detect_devices, set_speaker_volume, to_stereo

    rec, play = (cfg.audio_device, cfg.audio_device) if cfg.audio_device else detect_devices()
    print(f'收音裝置：{rec or "找不到"}\n播放裝置：{play or "找不到"}')
    if not rec or not play:
        raise RuntimeError('找不到 USB 音效卡：arecord -l / aplay -l 看看有沒有「USB PnP Audio Device」，'
                           '或在 smartlock.env 設 AUDIO_DEVICE=plughw:卡號,0')
    print(f'喇叭音量：{cfg.speaker_volume}%' if set_speaker_volume(play, cfg.speaker_volume)
          else '喇叭音量：沒有調整（沒有卡號或 amixer），沒聲音的話用 alsamixer 調大')
    with tempfile.TemporaryDirectory() as tmp:
        path = f'{tmp}/test.wav'
        input(f'按 Enter 後對著麥克風講話 {seconds} 秒…')
        subprocess.run(['arecord', '-q', '-D', rec, '-f', 'S16_LE', '-r', '16000', '-c', '1', '-d', str(seconds), path],
                       check=True)
        with wave.open(path, 'rb') as w:
            mono = np.frombuffer(w.readframes(w.getnframes()), '<i2')
        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) if len(mono) else 0.0
        peak = int(np.abs(mono.astype(np.int32)).max()) if len(mono) else 0
        print(f'錄到 {len(mono) / 16000:.1f} 秒；音量 RMS {rms:.0f}、峰值 {peak} / 32767'
              + ('（太小：alsamixer 按 F6 選 USB 卡，F4 調麥克風增益）' if peak < 1000 else '')
              + ('（破音：增益調低）' if peak >= 32000 else ''))
        for ch, where in (('left', '左聲道（門外）'), ('right', '右聲道（室內）')):
            input(f'按 Enter 從{where}播放…')
            subprocess.run(['aplay', '-q', '-D', play, '-f', 'S16_LE', '-r', '16000', '-c', '2', '-t', 'raw'],
                           input=to_stereo(mono, ch), check=True)
    try:
        out = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=5).stdout.strip()
        print(f'{out}' + ('' if out.endswith('=0x0') else '　← 不是 0x0：曾經欠壓或降頻，喇叭可能太大聲或電源不足'))
    except (OSError, subprocess.SubprocessError):
        pass


def set_gesture(cfg: Config, name=None, gesture=None):
    """綁定開鎖手勢。不加參數列出每個人目前的設定。"""
    from .face import read_map
    book = GestureBindings(cfg.gestures_file)
    people = {n: k for n, k in read_map(cfg.map_file).values()}
    if name is None:
        print('可用手勢：' + '、'.join(f'{g}（{GESTURE_LABEL[g]}）' for g in GESTURES))
        for n, k in sorted(people.items()):
            g = book.get(n)
            print(f'  {n:<12} {k:<7} {GESTURE_LABEL[g] if g else "未綁定（" + " / ".join(cfg.gesture_labels()) + "）"}')
        return
    if name not in people:
        raise ValueError(f'找不到 {name}，先用 capture + train 新增')
    gesture = None if str(gesture).lower() == 'none' else gesture
    if gesture is not None and gesture not in GESTURES:
        raise ValueError(f'手勢只能是：{", ".join(GESTURES)} 或 none')
    book.set(name, gesture)
    print(f'{name} 的開鎖手勢：{GESTURE_LABEL.get(book.get(name), "未綁定（預設）")}。正在執行的門鎖會自動套用。')


def menu(cfg: Config):
    while True:
        print('\n1. 採集人臉\n2. 訓練模型\n3. 啟動門鎖系統\n4. 測試 Arduino\n5. 離開'
              '\n6. 測試相機\n7. 啟動門鎖系統（模擬 Arduino）\n8. 測試手勢\n9. 哨兵紀錄與容量')
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
            elif choice == '9':
                show_records(cfg)
        except (RuntimeError, ValueError, OSError, cv2.error) as exc:
            print('錯誤：', exc)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    try:
        loaded = load_env_file(PROJECT_ROOT / ENV_FILE)
        if loaded:
            log.info('已讀取 %s（%d 項設定）', ENV_FILE, len(loaded))
        cfg = Config.from_env()
        commands = {'train': train, 'run': run, 'test-arduino': test_arduino,
                    'test-camera': test_camera, 'test-gesture': test_gesture,
                    'records': show_records, 'cleanup': cleanup, 'set-pin': set_pin, 'test-line': test_line,
                    'test-audio': test_audio}
        if cfg.enable_qr:
            commands['qr'] = show_qr
        if not argv:
            menu(cfg)
        elif argv[0] == 'capture' and len(argv) in (2, 3):
            capture(cfg, argv[1], parse_count(argv[2]) if len(argv) == 3 else None)
        elif argv[0] == 'check-face' and len(argv) in (1, 2):
            check_face(cfg, parse_seconds(argv[1]) if len(argv) == 2 else 20)
        elif argv[0] == 'set-gesture' and len(argv) in (1, 3):
            set_gesture(cfg, *argv[1:])
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
