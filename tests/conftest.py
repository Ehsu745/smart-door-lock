"""測試共用的假物件：假時鐘、假人臉辨識、假手勢、假 Arduino。不需要相機或任何硬體。"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smartlock.config import Config          # noqa: E402
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
        self.cfg = cfg or Config()
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
