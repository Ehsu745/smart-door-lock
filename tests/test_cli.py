"""cli：capture 張數、check-face（人臉檢查）。不需要相機：用假鏡頭 + conftest 的假人臉引擎。"""
import cv2
import numpy as np
import pytest

from smartlock import cli
from smartlock.cli import check_face, face_check_summary, parse_count, parse_seconds
from smartlock.config import Config
from smartlock.face import train


def test_parse_count_and_seconds():
    assert parse_count('8') == 8 and parse_count('200') == 200
    for bad in ('0', '201', '-3', 'abc', '8.5'):
        with pytest.raises(ValueError):
            parse_count(bad)
    assert parse_seconds('20') == 20 and parse_seconds('7.5') == 7.5
    for bad in ('4', '301', 'abc'):
        with pytest.raises(ValueError):
            parse_seconds(bad)


def test_main_passes_capture_count(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, 'load_env_file', lambda path: {})
    monkeypatch.setattr(cli, 'capture', lambda cfg, name, count=None: calls.append((name, count)))
    assert cli.main(['capture', 'eric', '8']) == 0
    assert cli.main(['capture', 'eric']) == 0
    assert cli.main(['capture', 'eric', 'x']) == 1             # 張數不合法 → 錯誤訊息，不會開相機
    assert calls == [('eric', 8), ('eric', None)]


def test_main_check_face_seconds(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, 'load_env_file', lambda path: {})
    monkeypatch.setattr(cli, 'check_face', lambda cfg, seconds: calls.append(seconds))
    assert cli.main(['check-face']) == 0
    assert cli.main(['check-face', '30']) == 0
    assert cli.main(['check-face', '1']) == 1
    assert calls == [20, 30]


def ok(d=30.0):
    return ('eric', d, True)


def miss(d=55.0):
    return ('Unknown', d, False)


def test_summary_counts_unlock_runs_and_verdict():
    s = face_check_summary([ok()] * 8 + [miss()] + [ok()] * 9 + [None], 50, 8)
    assert s['frames'] == 19 and s['faces'] == 18 and s['known'] == 17
    assert s['longest'] == 9 and s['unlock_runs'] == 2                # 每段連續 8 幀算一次（第 9 幀不重算）
    assert s['person'] == 'eric' and s['verdict'] == 'good'
    assert face_check_summary([miss()] * 3 + [ok()], 50, 8)['person'] == 'eric'   # 名字只看認得的幀
    assert set(s['rate']) == {40, 45, 50, 55}

    weak = face_check_summary([ok(45)] * 8 + [miss()] * 5, 50, 8)      # 開得了，但中位數 ≥ 40
    assert weak['unlock_runs'] == 1 and weak['verdict'] == 'weak'

    fail = face_check_summary([ok(45), miss()] * 10, 50, 8)            # 從來沒有連續 8 幀
    assert fail['unlock_runs'] == 0 and fail['verdict'] == 'fail'

    assert face_check_summary([None] * 5, 50, 8)['verdict'] == 'noface'
    assert face_check_summary([], 50, 8)['verdict'] == 'noface'


class FakeCamera:
    def __init__(self, frames):
        self.frames, self.i, self.closed = frames, 0, False

    def read(self):
        self.i += 1
        return self.frames[self.i % len(self.frames)]

    def close(self):
        self.closed = True


def test_check_face_end_to_end(tmp_path, capsys):
    cfg = Config(base=tmp_path)
    cfg.face_dir.mkdir()
    me = np.random.default_rng(1).integers(0, 255, (150, 150), dtype=np.uint8)
    for i in range(3):
        cv2.imwrite(str(cfg.face_dir / f'eric_{i}.jpg'), me)
    train(cfg)
    cam = FakeCamera([cv2.cvtColor(me, cv2.COLOR_GRAY2BGR)])
    s = check_face(cfg, seconds=0.3, camera=cam)
    assert cam.closed and s['person'] == 'eric' and s['verdict'] == 'good' and s['unlock_runs'] >= 1
    assert '結論：穩定' in capsys.readouterr().out
    assert sorted(p.name for p in cfg.face_dir.iterdir()) == ['eric_0.jpg', 'eric_1.jpg', 'eric_2.jpg']  # 不存照片

    stranger = np.random.default_rng(2).integers(0, 255, (150, 150), dtype=np.uint8)
    s = check_face(cfg, seconds=0.3, camera=FakeCamera([cv2.cvtColor(stranger, cv2.COLOR_GRAY2BGR)]))
    out = capsys.readouterr().out
    assert s['verdict'] == 'fail' and s['person'] is None
    assert 'capture 你的名字 8' in out and 'eric' not in out    # 陌生人不能被建議補拍成 eric（模板污染）
