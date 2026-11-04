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


def test_wrong_gesture_times_out_and_can_retry(h):
    h.step(1.0, [MASTER]).step(h.cfg.gesture_window + 0.2, [MASTER], 'Open_Palm')
    assert h.unlocks == 0
    assert 'gesture_timeout' in h.kinds()
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
