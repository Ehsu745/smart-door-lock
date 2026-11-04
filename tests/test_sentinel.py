"""哨兵錄影與 SD 卡容量管理。用假時鐘逐幀模擬，實際寫出影片檔再讀回來檢查。"""
import cv2
import numpy as np
import pytest

from smartlock.config import Config
from smartlock.events import EventBus
from smartlock.face import FaceResult
from smartlock.sentinel import Sentinel
from smartlock.storage import MB, Storage

from conftest import MASTER, Harness

FPS = 10
DT = 0.1                                   # 測試用 10 fps 迴圈，和錄影幀率相同
FRAME = np.full((120, 160, 3), 80, np.uint8)
STRANGER = FaceResult((40, 30, 90, 90), 2, 110.0, 'Unknown', 'Stranger', False)
FAR_STRANGER = FaceResult((40, 30, 50, 50), 2, 110.0, 'Unknown', 'Stranger', False)


class Clock:
    def __init__(self):
        self.t = 1000.0


def make(tmp_path, disk_free_mb=50_000, **kw):
    cfg = Config(base=tmp_path, sentinel_fps=FPS, **kw)
    storage = Storage(cfg, wallclock=lambda: 1_800_000_000.0,
                      disk_usage=lambda p: (100_000 * MB, 0, disk_free_mb * MB))
    bus, events = EventBus(), []
    bus.subscribe('*', events.append)
    clock = Clock()
    sentinel = Sentinel(cfg, storage, bus, background=False)
    return cfg, storage, sentinel, events, clock


def run(sentinel, clock, seconds, faces=(), frame=FRAME, dt=DT):
    for _ in range(round(seconds / dt)):
        sentinel.observe(frame, list(faces), clock.t)
        clock.t += dt


def frame_count(path):
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


# ---- 觸發條件 ----
def test_short_visit_does_not_record(tmp_path):
    _, storage, s, events, c = make(tmp_path)
    run(s, c, 3, [STRANGER])
    run(s, c, 5)
    assert s.state == 'idle' and storage.list_records() == []


def test_stranger_over_trigger_records_with_preroll(tmp_path):
    cfg, storage, s, events, c = make(tmp_path)
    run(s, c, 2)                                    # 陌生人出現前的畫面也要進預錄
    run(s, c, 8, [STRANGER])
    assert s.state == 'recording'
    run(s, c, cfg.sentinel_stop + 0.5)
    assert s.state == 'idle'
    [rec] = storage.list_records()
    assert rec['status'] == 'done' and rec['notified'] == 1 and rec['min_distance'] == 110.0
    # 預錄 5 秒 + 觸發後 3 秒 + 離開後 5 秒 ≈ 13 秒
    assert 12 <= rec['duration'] <= 14.5
    n = frame_count(storage.path(rec['video']))
    assert abs(n - rec['duration'] * FPS) <= 3      # 影片長度和實際時間一致
    assert storage.path(rec['snapshot']).exists() and rec['size_bytes'] > 0
    kinds = [e.kind for e in events]
    assert kinds == ['stranger_detected', 'sentinel_saved']
    assert events[0].data == {'record_id': rec['id'], 'notify': True}


def test_master_cancels_watching(tmp_path):
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 3, [STRANGER])
    run(s, c, 0.2, [MASTER])
    run(s, c, 4, [STRANGER])                        # 重新計時，3+4 秒但不連續 → 不錄
    assert s.state == 'watching' and storage.list_records() == []


def test_brief_detection_gap_tolerated(tmp_path):
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 3, [STRANGER])
    run(s, c, 1)                                    # 偵測漏掉 1 秒（< face_grace）
    run(s, c, 2.5, [STRANGER])
    assert s.state == 'recording'


def test_far_away_face_ignored(tmp_path):
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 10, [FAR_STRANGER])
    assert s.state == 'idle'


def test_known_people_not_recorded(tmp_path):
    from conftest import GUEST
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 10, [GUEST])
    assert s.state == 'idle'


def test_long_stay_split_into_segments(tmp_path):
    cfg, storage, s, events, c = make(tmp_path, sentinel_max=20)
    run(s, c, 5 + 45, [STRANGER])
    run(s, c, 6)
    recs = storage.list_records()
    assert len(recs) == 3                            # 20 + 20 + 剩下的
    assert [e.kind for e in events].count('stranger_detected') == 1   # 只通知一次


def test_notify_cooldown(tmp_path):
    cfg, storage, s, events, c = make(tmp_path, sentinel_cooldown=60)
    for _ in range(2):
        run(s, c, 6, [STRANGER]); run(s, c, 6)
    run(s, c, 60)
    run(s, c, 6, [STRANGER]); run(s, c, 6)
    flags = [e.data['notify'] for e in events if e.kind == 'stranger_detected']
    assert flags == [True, False, True]


def test_slow_loop_keeps_real_time(tmp_path):
    """辨識迴圈只有 4 fps 時，影片仍要是 10 fps 的真實長度（重複幀補齊）。"""
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 10, [STRANGER], dt=0.25)
    run(s, c, 6, dt=0.25)
    [rec] = storage.list_records()
    assert abs(frame_count(storage.path(rec['video'])) - rec['duration'] * FPS) <= 4


def test_close_finishes_open_recording(tmp_path):
    _, storage, s, _, c = make(tmp_path)
    run(s, c, 7, [STRANGER])
    s.close(c.t)
    [rec] = storage.list_records()
    assert rec['status'] == 'done' and frame_count(storage.path(rec['video'])) > 0


def test_low_disk_skips_recording(tmp_path):
    _, storage, s, events, c = make(tmp_path, disk_free_mb=100)
    run(s, c, 10, [STRANGER])
    assert storage.list_records() == [] and s.state != 'recording'


def test_background_writer(tmp_path):
    cfg = Config(base=tmp_path, sentinel_fps=FPS)
    storage = Storage(cfg, disk_usage=lambda p: (100_000 * MB, 0, 50_000 * MB))
    s = Sentinel(cfg, storage, EventBus(), background=True)
    c = Clock()
    run(s, c, 7, [STRANGER]); run(s, c, 6)
    s.close()
    [rec] = storage.list_records()
    assert rec['status'] == 'done' and frame_count(storage.path(rec['video'])) > 50


# ---- 和 DoorSystem 整合 ----
def test_door_system_frame_hook(tmp_path):
    h = Harness(Config(base=tmp_path, sentinel_fps=20))
    storage = Storage(h.cfg, disk_usage=lambda p: (100_000 * MB, 0, 50_000 * MB))
    s = Sentinel(h.cfg, storage, h.system.bus, background=False)
    h.system.frame_hooks.append(s.observe)
    h.system.providers['sentinel'] = lambda: s.status(h.clock())
    from conftest import STRANGER as DOOR_STRANGER
    h.step(6, [DOOR_STRANGER])
    assert h.system.snapshot()['sentinel']['state'] == 'recording'
    assert 'stranger_detected' in h.kinds() and h.unlocks == 0


# ---- 容量管理 ----
def _fake_record(storage, started, mb):
    rid = storage.start_record(started, f'clips/r{started:.0f}.avi', False)
    storage.path(f'clips/r{started:.0f}.avi').write_bytes(b'x' * int(mb * MB))
    storage.finish_record(rid, 10, None, None)
    return rid


def test_retention_by_size(tmp_path):
    cfg = Config(base=tmp_path, record_max_mb=3)
    st = Storage(cfg, wallclock=lambda: 1_800_000_000.0)
    ids = [_fake_record(st, 1_800_000_000 - 100 + i, 1) for i in range(5)]
    assert st.enforce() == 2
    assert [r['id'] for r in st.list_records()] == ids[:1:-1]           # 最舊的兩段被刪
    assert not st.path(f'clips/r{1_800_000_000 - 100:.0f}.avi').exists()


def test_retention_by_age(tmp_path):
    now = 1_800_000_000.0
    cfg = Config(base=tmp_path, record_max_days=7)
    st = Storage(cfg, wallclock=lambda: now)
    _fake_record(st, now - 8 * 86400, 0.1)
    keep = _fake_record(st, now - 1 * 86400, 0.1)
    st.enforce()
    assert [r['id'] for r in st.list_records()] == [keep]


def test_retention_by_free_space(tmp_path):
    cfg = Config(base=tmp_path, record_min_free_mb=1024)
    free = {'mb': 900}

    def disk(p):
        return 100_000 * MB, 0, free['mb'] * MB
    st = Storage(cfg, wallclock=lambda: 1_800_000_000.0, disk_usage=disk)
    _fake_record(st, 1, 0.1)
    _fake_record(st, 2, 0.1)

    orig = st.delete
    def delete(rid):                                  # 每刪一段假裝釋放 100 MB
        free['mb'] += 100
        return orig(rid)
    st.delete = delete
    assert st.enforce() == 2 and free['mb'] == 1100


def test_unfinished_record_marked_failed_on_restart(tmp_path):
    cfg = Config(base=tmp_path)
    st = Storage(cfg)
    rid = st.start_record(1, 'clips/x.avi', False)
    st.close()
    assert Storage(cfg).get(rid)['status'] == 'failed'


def test_path_traversal_rejected(tmp_path):
    st = Storage(Config(base=tmp_path))
    with pytest.raises(ValueError):
        st.path('../face.yml')


def test_event_log_skips_seen(tmp_path):
    from smartlock.events import Event
    st = Storage(Config(base=tmp_path))
    st.log_event(Event('seen', '看到 eric'))
    st.log_event(Event('unlock', '手動解鎖 → UNLOCK 成功'))
    assert [e['kind'] for e in st.list_events()] == ['unlock']
