"""設定、手勢規則、Arduino 通訊協定、QR code、訓練。"""
import os
import select
import sys
import threading
import time

import cv2
import numpy as np
import pytest

from smartlock.arduino import Arduino
from smartlock.config import Config
from smartlock.events import Event, EventBus
from smartlock.face import FaceRecognizer, read_map, train
from smartlock.gesture import classify_landmarks


# ==========================================
# 設定
# ==========================================
def test_config_defaults_match_previous_version():
    c = Config.from_env({})
    assert (c.threshold, c.required_frames, c.rearm_seconds, c.unlock_seconds) == (70.0, 8, 3.0, 8.0)
    assert c.allowed_gestures == ('Thumb_Up', 'Victory') and c.cmd_eol == '\n' and c.ack_strict
    assert c.enable_qr and not c.simulate and c.web_port == 5050


def test_config_env_overrides():
    c = Config.from_env({'FACE_THRESHOLD': '55', 'ALLOWED_GESTURES': 'Victory', 'CMD_EOL': 'none',
                         'ARDUINO_STRICT': '0', 'REQUIRE_GESTURE': '0', 'SIM_ARDUINO': '1', 'CONTROL_PIN': '9'})
    assert c.threshold == 55 and c.allowed_gestures == ('Victory',) and c.cmd_eol == ''
    assert not c.ack_strict and not c.require_gesture and c.simulate and c.control_pin == '9'


def test_config_rejects_bad_eol():
    with pytest.raises(ValueError):
        Config.from_env({'CMD_EOL': 'windows'})


def test_event_bus_wildcard_and_kind():
    bus, got = EventBus(), []
    bus.subscribe('unlock', lambda e: got.append(('unlock', e.text)))
    bus.subscribe('*', lambda e: got.append(('*', e.kind)))
    bus.publish(Event('unlock', 'x'))
    bus.publish(Event('seen', 'y'))
    assert got == [('unlock', 'x'), ('*', 'unlock'), ('*', 'seen')]


# ==========================================
# 手勢規則（備援模式）
# ==========================================
def _hand(fingers, thumb):
    pts = [(0.5, 0.9)] + [(0, 0)] * 20
    for f, (x, i) in {'index': (0.44, 5), 'middle': (0.5, 9), 'ring': (0.56, 13), 'pinky': (0.62, 17)}.items():
        pts[i] = (x, 0.65)
        if f in fingers:
            pts[i + 1], pts[i + 2], pts[i + 3] = (x, 0.52), (x, 0.44), (x, 0.36)
        else:
            pts[i + 1], pts[i + 2], pts[i + 3] = (x, 0.56), (x, 0.66), (x, 0.70)
    thumbs = {'up': [(0.40, 0.78), (0.36, 0.68), (0.35, 0.55), (0.35, 0.42)],
              'down': [(0.40, 0.80), (0.36, 0.84), (0.35, 0.95), (0.35, 1.05)],
              'side': [(0.40, 0.80), (0.33, 0.74), (0.27, 0.70), (0.21, 0.66)],
              'in': [(0.44, 0.80), (0.45, 0.72), (0.47, 0.68), (0.49, 0.67)]}
    pts[1:5] = thumbs[thumb]
    return pts


@pytest.mark.parametrize('expected,fingers,thumb', [
    ('Thumb_Up', [], 'up'), ('Thumb_Down', [], 'down'), ('Victory', ['index', 'middle'], 'in'),
    ('Open_Palm', ['index', 'middle', 'ring', 'pinky'], 'side'), ('Closed_Fist', [], 'in'),
    ('Pointing_Up', ['index'], 'in'), ('ILoveYou', ['index', 'pinky'], 'side')])
def test_gesture_rules(expected, fingers, thumb):
    assert classify_landmarks(_hand(fingers, thumb)) == expected


# ==========================================
# Arduino 通訊協定（用 pty 模擬學弟的 sketch）
# ==========================================
def _fake_sketch(master, reply=True, stop=None, unknown=False):
    """模擬 door_lock.ino：\\n 或 \\r 結尾、不分大小寫、回覆 Unlocked.../Locked。
    unknown=True 模擬「看不懂指令」的情況，一律回 Unknown command。"""
    buf = ''
    while not stop.is_set():
        r, _, _ = select.select([master], [], [], 0.05)
        if not r:
            continue
        for ch in os.read(master, 100).decode():
            if ch in '\r\n':
                cmd, buf = buf.strip().lower(), ''
                if not cmd:
                    continue
                if unknown:
                    os.write(master, b'Unknown command: use unlock or lock\r\n')
                elif reply and cmd == 'unlock':
                    os.write(master, b'Unlocked: auto-lock in 8 seconds\r\n')
                elif reply and cmd == 'lock':
                    os.write(master, b'Locked\r\n')
            else:
                buf += ch


@pytest.fixture
def sketch():
    if not sys.platform.startswith('linux'):
        pytest.skip('pty 測試只在 Linux 執行')
    pytest.importorskip('serial')
    import pty
    import tty
    started = []

    def start(reply=True, unknown=False):
        master, slave = pty.openpty()
        tty.setraw(slave)
        stop = threading.Event()
        threading.Thread(target=_fake_sketch, args=(master, reply, stop, unknown), daemon=True).start()
        started.append(stop)
        return os.ttyname(slave)
    yield start
    for s in started:
        s.set()


def test_arduino_protocol_with_replying_sketch(sketch, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda s: None)          # 跳過開埠後的 2 秒等待
    a = Arduino(Config(arduino_port=sketch()))
    assert a.connect()
    assert a.send('UNLOCK')[0] and a.send('LOCK')[0]
    a.close()


def test_arduino_strict_rejects_silent_sketch(sketch, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    assert not Arduino(Config(arduino_port=sketch(reply=False))).connect()


def test_arduino_non_strict_accepts_silent_sketch(sketch, monkeypatch):
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    a = Arduino(Config(arduino_port=sketch(reply=False), ack_strict=False))
    assert a.connect() and a.send('UNLOCK')[0]
    a.close()


def test_reply_matching():
    assert Arduino.matches('UNLOCK', 'Unlocked: auto-lock in 8 seconds')
    assert Arduino.matches('LOCK', 'Locked')
    assert not Arduino.matches('LOCK', 'Unlocked: auto-lock in 8 seconds')
    assert not Arduino.matches('UNLOCK', 'Ready: enter unlock or lock')
    assert not Arduino.matches('UNLOCK', 'Unknown command: use unlock or lock')
    assert not Arduino.matches('LOCK', 'Unknown command: use unlock or lock')


def test_wrong_command_format_is_not_acknowledged(sketch, monkeypatch):
    """指令格式錯（例如大寫被 sketch 拒絕）時，嚴格模式必須回報失敗，而不是誤判成功。"""
    monkeypatch.setattr(time, 'sleep', lambda s: None)
    a = Arduino(Config(arduino_port=sketch(reply=True, unknown=True)))
    assert not a.connect()


# ==========================================
# QR code
# ==========================================
def test_qr_image_decodes():
    pytest.importorskip('qrcode')
    from smartlock.qr import qr_image
    url = 'http://192.168.0.50:5050'
    assert cv2.QRCodeDetector().detectAndDecode(qr_image(url, 300))[0] == url


# ==========================================
# 訓練與辨識（用合成影像，不需要真人照片）
# ==========================================
class WholeImageDetector:
    """假偵測器：整張圖就是一張臉。"""

    def detectMultiScale(self, gray, *args, **kwargs):
        h, w = gray.shape[:2]
        return [(0, 0, w, h)]


def test_train_and_identify(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.face_dir.mkdir()
    rng = np.random.default_rng(0)
    people = {'eric': rng.integers(0, 255, (150, 150), dtype=np.uint8),
              'amy': rng.integers(0, 255, (150, 150), dtype=np.uint8)}
    for name, img in people.items():
        for i in range(5):
            cv2.imwrite(str(cfg.face_dir / f'{name}_{i}.jpg'), img)
    cfg.map_file.write_text('1:eric:guest\n', encoding='utf-8')     # 手動設定的 guest 要被保留
    counts = train(cfg)
    assert counts == {'amy': 5, 'eric': 5}
    mapping = read_map(cfg.map_file)
    assert sorted(mapping.values()) == [('amy', 'master'), ('eric', 'guest')]

    rec = FaceRecognizer.load(cfg)
    rec.detector = WholeImageDetector()
    result = rec.identify(people['amy'])[0]
    assert result.person == 'amy' and result.is_master and result.distance < cfg.threshold
    assert rec.identify(people['eric'])[0].is_guest
