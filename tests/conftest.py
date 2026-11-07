"""測試共用的假物件：假時鐘、假人臉辨識、假手勢、假 Arduino。不需要相機或任何硬體。"""
import dataclasses
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smartlock.config import PROJECT_ROOT, Config   # noqa: E402
from smartlock.door import DoorSystem         # noqa: E402
from smartlock.face import FaceResult         # noqa: E402

FRAME = np.zeros((480, 640, 3), np.uint8)
DT = 0.05                                     # 每幀 50 ms（20 fps）


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def face(person='eric', kind='master', label=0, distance=40.0, known=True):
    return FaceResult((100, 100, 150, 150), label, distance, person, kind, known)


MASTER = face()
GUEST = face('amy', 'guest', label=1)
STRANGER = face('Unknown', 'Stranger', label=2, distance=120.0, known=False)


class FakeFaceEngine:
    """假 YuNet + SFace：整張圖就是一張臉；特徵 = 縮成 16×8 的像素。
    同一張圖特徵幾乎相同，不同的隨機雜訊圖彼此幾乎正交；全平的圖當作找不到五官。"""

    def detect(self, bgr):
        h, w = bgr.shape[:2]
        return np.array([[0, 0, w, h] + [0] * 10 + [1.0]], np.float32)

    def feature(self, img, face_row=None):
        g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        v = cv2.resize(g, (16, 8), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
        v -= v.mean()
        return v / (np.linalg.norm(v) + 1e-9)

    def embed_photo(self, gray):
        return None if gray.std() < 1 else self.feature(gray)


@pytest.fixture(autouse=True)
def fake_face_engine(monkeypatch):
    """所有測試都用假的人臉引擎：不需要下載模型，也不碰專案的 models/。"""
    import smartlock.face as face_module
    monkeypatch.setattr(face_module, 'make_engine', lambda cfg: FakeFaceEngine())


class FakeRecognizer:
    """identify() 回傳目前設定的人臉清單。"""

    def __init__(self):
        self.faces = []

    def identify(self, gray):
        return list(self.faces)


class FakeGesture:
    def __init__(self):
        self.name = None
        self.score = 0.0

    def detect(self, frame):
        if self.name is None:
            return None, 0.0, None
        return self.name, self.score, [(300 + 5 * i, 300 + 3 * i) for i in range(21)]


class RecArduino:
    def __init__(self, ok=True):
        self.ok = ok
        self.commands = []
        self.status = '測試'
        self.connected = True

    def send(self, command):
        self.commands.append(command)
        return (True, 'ok') if self.ok else (False, '模擬失敗')


class Harness:
    """把 DoorSystem 和所有假物件包在一起，提供 step(秒數, faces=..., gesture=...)。"""

    def __init__(self, cfg=None, with_gesture=True, arduino=None):
        cfg = cfg or Config()
        if Path(cfg.base) == PROJECT_ROOT:          # 測試不碰專案資料夾（Pi 上可能有真的 pin.json、records/）
            cfg = dataclasses.replace(cfg, base=Path(tempfile.mkdtemp(prefix='smartlock-test-')))
        self.cfg = cfg
        self.clock = FakeClock()
        self.recognizer = FakeRecognizer()
        self.gesture = FakeGesture() if with_gesture else None
        self.arduino = arduino or RecArduino()
        self.system = DoorSystem(self.cfg, self.arduino, self.recognizer, self.gesture, clock=self.clock)
        self.events = []
        self.system.bus.subscribe('*', self.events.append)

    def step(self, seconds, faces=(), gesture=None, score=0.93):
        self.recognizer.faces = list(faces)
        if self.gesture is not None:
            self.gesture.name, self.gesture.score = gesture, score
        for _ in range(max(1, round(seconds / DT))):
            self.system.process_frame(FRAME.copy())
            self.clock.t += DT
        return self

    @property
    def unlocks(self):
        return self.arduino.commands.count('UNLOCK')

    def kinds(self):
        return [e.kind for e in self.events]

    @property
    def phase(self):
        return self.system.state['phase']


@pytest.fixture
def h():
    return Harness()
