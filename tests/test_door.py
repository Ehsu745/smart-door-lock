"""DoorSystem 狀態機：用假時鐘逐幀驗證開鎖規則。"""
from smartlock.config import Config
from smartlock.door import MULTI_FACE

from conftest import GUEST, MASTER, STRANGER, Harness, RecArduino


# ---- master：人臉 + 手勢 ----
def test_master_face_then_gesture_unlocks_once(h):
    h.step(1.0, [MASTER])
    assert h.phase == 'gesture' and h.unlocks == 0
    h.step(0.5, [MASTER], 'Thumb_Up')
    assert h.unlocks == 1
    assert h.kinds().count('face_passed') == 1 and 'unlock' in h.kinds()
    assert h.system.snapshot()['unlocked'] is True


def test_victory_also_accepted(h):
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Victory')
    assert h.unlocks == 1


def test_wrong_gesture_fails_fast_and_can_retry(h):
    h.step(1.0, [MASTER]).step(1.0, [MASTER], 'Open_Palm')     # 一直比錯：15 幀（約 0.75 秒）就失敗
    assert h.unlocks == 0
    assert 'gesture_wrong' in h.kinds() and 'gesture_timeout' not in h.kinds()
    h.step(h.cfg.retry_cooldown + 0.2, [MASTER])          # 冷卻後重新進入手勢階段
    assert h.phase == 'gesture'
    h.step(0.5, [MASTER], 'Thumb_Up')
    assert h.unlocks == 1


def test_low_confidence_gesture_not_accepted(h):
    h.step(1.0, [MASTER]).step(1.0, [MASTER], 'Thumb_Up', score=0.3)
    assert h.unlocks == 0


def test_gesture_without_face_does_nothing(h):
    h.step(3.0, [], 'Thumb_Up')
    assert h.unlocks == 0 and h.phase == 'face'


def test_leaving_during_gesture_cancels():
    cfg = Config(gesture_window=10, face_grace=1.0)
    h = Harness(cfg)
    h.step(1.0, [MASTER]).step(1.5, [])
    assert h.phase == 'face' and 'gesture_cancel' in h.kinds()
    assert h.unlocks == 0


def test_hand_covering_face_briefly_is_tolerated(h):
    h.step(1.0, [MASTER]).step(1.0, [], 'Thumb_Up')       # 手擋住臉 1 秒（< face_grace），手勢照樣成立
    assert h.unlocks == 1


# ---- 開鎖後不重複開 ----
def test_no_repeat_unlock_while_staying(h):
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up').step(5.0, [MASTER], 'Thumb_Up')
    assert h.unlocks == 1


def test_rearm_after_leaving(h):
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    h.step(h.cfg.rearm_seconds + 0.2, [])
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    assert h.unlocks == 2


def test_short_absence_does_not_rearm(h):
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    h.step(h.cfg.rearm_seconds - 1.0, [])
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    assert h.unlocks == 1


# ---- 只用人臉模式 ----
def test_face_only_mode_unlocks_directly():
    h = Harness(Config(require_gesture=False), with_gesture=False)
    h.step(1.0, [MASTER])
    assert h.unlocks == 1 and 'face_passed' not in h.kinds()


# ---- guest ----
def test_guest_never_auto_unlocks(h):
    h.step(1.0, [GUEST]).step(2.0, [GUEST], 'Thumb_Up')
    assert h.unlocks == 0 and h.phase == 'face'
    assert h.kinds().count('guest_detected') == 1
    assert h.system.state['visitor'] == 'amy'


def test_guest_renotified_after_leaving(h):
    h.step(1.0, [GUEST]).step(h.cfg.rearm_seconds + 0.2, []).step(1.0, [GUEST])
    assert h.kinds().count('guest_detected') == 2


def test_visitor_banner_cleared_after_hold(h):
    h.step(1.0, [GUEST]).step(h.cfg.visitor_hold + 0.2, [])
    assert h.system.state['visitor'] is None


def test_manual_unlock_clears_visitor(h):
    h.step(1.0, [GUEST])
    h.system.door('UNLOCK', '手動解鎖')
    assert h.unlocks == 1 and h.system.state['visitor'] is None


# ---- 其他不應開鎖的情況 ----
def test_stranger_never_unlocks(h):
    h.step(3.0, [STRANGER], 'Thumb_Up')
    assert h.unlocks == 0 and h.system.state['name'] == 'Unknown'


def test_multiple_faces_block_unlock(h):
    h.step(3.0, [MASTER, GUEST], 'Thumb_Up')
    assert h.unlocks == 0 and h.system.state['name'] == MULTI_FACE


def test_auto_off_blocks_unlock(h):
    h.system.update(auto=False)
    h.step(1.0, [MASTER]).step(1.0, [MASTER], 'Thumb_Up')
    assert h.unlocks == 0


def test_streak_must_be_consecutive(h):
    for _ in range(5):                                     # 認出幾幀就斷掉，永遠湊不滿 required_frames
        h.step(0.15, [MASTER]).step(0.05, [STRANGER])
    assert h.phase == 'face'


# ---- 事件與失敗處理 ----
def test_seen_event_only_when_name_changes(h):
    h.step(0.5, [STRANGER]).step(0.5, [STRANGER])
    assert h.kinds().count('seen') == 1


def test_door_failure_emits_event():
    h = Harness(arduino=RecArduino(ok=False))
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    assert 'door_failed' in h.kinds() and 'unlock' not in h.kinds()
    assert h.system.snapshot()['unlocked'] is False


def test_broken_subscriber_does_not_break_state_machine(h):
    def boom(event):
        raise RuntimeError('subscriber bug')
    h.system.bus.subscribe('*', boom)
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    assert h.unlocks == 1


def test_snapshot_contains_recent_events(h):
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Thumb_Up')
    events = h.system.snapshot()['events']
    assert events and '→ 已開鎖' in events[0]


def test_no_hand_times_out(h):
    h.step(1.0, [MASTER]).step(h.cfg.gesture_window + 0.2, [MASTER])
    assert h.unlocks == 0 and 'gesture_timeout' in h.kinds()


# ==========================================
# 每個主人綁定自己的開鎖手勢（gestures.json）
# ==========================================
def bound_harness(tmp_path, **binding):
    from smartlock.bindings import GestureBindings
    h = Harness(Config(base=tmp_path))
    h.system.bindings = GestureBindings(tmp_path / 'gestures.json')
    for name, g in binding.items():
        h.system.bindings.set(name, g)
    return h


def test_bound_gesture_is_the_only_one(tmp_path):
    h = bound_harness(tmp_path, eric='Thumb_Down')
    h.step(1.0, [MASTER]).step(1.0, [MASTER], 'Thumb_Up')       # 預設的讚不能再用
    assert h.unlocks == 0 and 'gesture_wrong' in h.kinds()
    h.step(h.cfg.retry_cooldown + 0.2, [MASTER]).step(0.5, [MASTER], 'Thumb_Down')
    assert h.unlocks == 1


def test_unbound_master_keeps_default_gestures(tmp_path):
    h = bound_harness(tmp_path, yang='ILoveYou')                 # 只有別人有綁定
    h.step(1.0, [MASTER]).step(0.5, [MASTER], 'Victory')
    assert h.unlocks == 1


def test_all_seven_gestures_can_be_bound(tmp_path):
    from smartlock.bindings import GESTURES
    assert len(GESTURES) == 7
    for g in GESTURES:
        h = bound_harness(tmp_path, eric=g)
        h.step(1.0, [MASTER]).step(0.5, [MASTER], g)
        assert h.unlocks == 1, g


def test_cycling_gestures_is_caught(tmp_path):
    """有人騙過人臉後輪流試手勢：比錯的幀數累計，不是連續才算。"""
    h = bound_harness(tmp_path, eric='ILoveYou')
    h.step(1.0, [MASTER])
    for g in ['Thumb_Up', 'Victory', 'Open_Palm', 'Closed_Fist', 'Pointing_Up', 'Thumb_Down']:
        h.step(0.15, [MASTER], g)                                 # 每個只比 3 幀，連續性門檻抓不到
    h.step(0.5, [MASTER], 'ILoveYou')
    assert h.unlocks == 0 and 'gesture_wrong' in h.kinds()


def test_brief_transition_is_tolerated(tmp_path):
    """比 YA 的過程中會短暫被認成食指朝上，不能因此失敗。"""
    h = bound_harness(tmp_path, eric='Victory')
    h.step(1.0, [MASTER]).step(0.2, [MASTER], 'Pointing_Up').step(0.5, [MASTER], 'Victory')
    assert h.unlocks == 1


def test_low_confidence_or_none_is_not_counted_as_wrong(tmp_path):
    h = bound_harness(tmp_path, eric='Victory')
    h.step(1.0, [MASTER]).step(2.0, [MASTER], 'Thumb_Up', score=0.3).step(2.0, [MASTER], 'None')
    assert 'gesture_wrong' not in h.kinds() and h.phase == 'gesture'


def test_bound_gesture_not_revealed(tmp_path):
    """門口畫面、狀態、事件文字都不寫出綁定的是哪個手勢 —— 包括開鎖那一幀。"""
    h = bound_harness(tmp_path, eric='ILoveYou')
    h.step(1.0, [MASTER])
    st = h.system.snapshot()
    assert st['phase'] == 'gesture' and st['target_bound'] is True
    h.step(0.5, [MASTER], 'ILoveYou')
    assert h.unlocks == 1
    st = h.system.snapshot()
    assert st['gesture'] is None and st['gesture_score'] is None
    texts = ' '.join(e.text for e in h.events) + str(st)
    assert 'ILoveYou' not in texts and 'I Love' not in texts
    assert '綁定手勢' in [e.text for e in h.events if e.kind == 'unlock'][0]


def test_unbound_status_still_shows_gesture(tmp_path):
    h = bound_harness(tmp_path)
    h.step(1.0, [MASTER]).step(0.1, [MASTER], 'Thumb_Up')
    assert h.system.snapshot()['gesture'] == 'Thumb_Up' and h.system.snapshot()['target_bound'] is False


def test_bindings_reload_and_validation(tmp_path):
    import os
    import pytest
    from smartlock.bindings import GestureBindings
    running = GestureBindings(tmp_path / 'gestures.json')
    GestureBindings(tmp_path / 'gestures.json').set('eric', 'Victory')          # 例如在 Pi 上用 set-gesture 改
    assert running.get('eric') == 'Victory'
    assert os.stat(tmp_path / 'gestures.json').st_mode & 0o077 == 0              # 只有自己能讀
    running.set('eric', None)
    assert running.get('eric') is None
    with pytest.raises(ValueError):
        running.set('eric', 'Middle_Finger')
    (tmp_path / 'gestures.json').write_text('{壞掉的 json')
    assert running.get('eric') is None                                            # 檔案壞掉不會讓門鎖掛掉


# ---- 省電：沒人、畫面沒動時少找幾次人臉 ----
from conftest import DT, FRAME                                        # noqa: E402


class CountingRecognizer:
    def __init__(self):
        self.faces, self.calls = [], 0

    def identify(self, gray):
        self.calls += 1
        return list(self.faces)


def idle_harness(**cfg):
    h = Harness(Config(**cfg))
    h.recognizer = h.system.recognizer = CountingRecognizer()
    h.hooked = []
    h.system.frame_hooks.append(lambda frame, faces, now: h.hooked.append(len(faces)))
    return h


def run(h, seconds, frame=FRAME, faces=()):
    h.recognizer.faces = list(faces)
    n = max(1, round(seconds / DT))
    before = h.recognizer.calls
    for _ in range(n):
        h.system.step(frame.copy())
        h.clock.t += DT
    return h.recognizer.calls - before, n


def moved():
    f = FRAME.copy()
    f[100:300, 200:400] = 255 - f[100:300, 200:400]                  # 有人走進來：一大塊變了
    return f


def test_idle_detects_only_a_few_times_per_second():
    h = idle_harness(idle_after=3, idle_fps=2)
    calls, n = run(h, 2.5)
    assert calls == n                                                 # 剛啟動：每幀都找
    run(h, 1.0)
    calls, n = run(h, 4.0)
    assert 7 <= calls <= 9 and n > 40                                 # 省電：每秒約 2 次
    assert h.system.snapshot()['idle'] is True
    assert len(h.hooked) == round(7.5 / DT) and set(h.hooked) == {0}  # 省電時每幀照樣交給哨兵（預錄緩衝）


def test_motion_wakes_up_immediately():
    h = idle_harness(idle_after=3, idle_fps=2)
    run(h, 5.0)
    assert h.system.idle
    before = h.recognizer.calls
    h.system.step(moved())
    assert h.recognizer.calls == before + 1 and not h.system.idle    # 動的那一幀就找
    calls, n = run(h, 2.0, moved())                                   # 停在新位置也維持全速 idle_after 秒
    assert calls == n


def test_face_keeps_full_speed_even_when_still():
    h = idle_harness(idle_after=3, idle_fps=2)
    calls, n = run(h, 6.0, faces=[STRANGER])
    assert calls == n and not h.system.idle


def test_still_face_found_by_idle_check_wakes_up():
    h = idle_harness(idle_after=3, idle_fps=2)
    run(h, 5.0)
    calls, _ = run(h, 0.6, faces=[STRANGER])                          # 站著不動：下一次例行檢查就看到
    assert calls >= 1
    calls, n = run(h, 2.0, faces=[STRANGER])
    assert calls == n


def test_gesture_phase_never_idles():
    h = idle_harness(idle_after=0.5, idle_fps=1)
    run(h, 1.0, faces=[MASTER])
    assert h.phase == 'gesture'
    calls, n = run(h, 1.0)                                            # 手擋住臉、畫面也沒動
    assert calls == n


def test_idle_disabled():
    h = idle_harness(idle_after=0)
    calls, n = run(h, 6.0)
    assert calls == n and h.system.snapshot()['idle'] is False


def test_idle_config_from_env():
    c = Config.from_env({'IDLE_AFTER': '5', 'IDLE_FPS': '1', 'MOTION_RATIO': '0.02'})
    assert (c.idle_after, c.idle_fps, c.motion_ratio) == (5, 1, 0.02)
    assert Config.from_env({}).idle_after == 3
