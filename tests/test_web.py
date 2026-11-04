"""Flask 控制頁：PIN、指令、狀態欄位。"""
import pytest

pytest.importorskip('flask')

from smartlock.config import Config
from smartlock.web import create_web

from conftest import Harness


def client(pin=''):
    h = Harness(Config(control_pin=pin))
    return h, create_web(h.system).test_client()


def test_index_and_static_files():
    _, c = client()
    assert '智慧門鎖' in c.get('/').get_data(as_text=True)
    assert c.get('/static/app.js').status_code == 200
    assert c.get('/static/style.css').status_code == 200


def test_status_fields():
    _, c = client()
    d = c.get('/api/status').get_json()
    for key in ('name', 'phase', 'auto', 'events', 'arduino', 'connected', 'pin_required', 'unlocked', 'allowed'):
        assert key in d


def test_unlock_without_pin():
    h, c = client()
    r = c.post('/api/action', json={'action': 'unlock'})
    assert r.status_code == 200 and h.arduino.commands == ['UNLOCK']


@pytest.mark.parametrize('pin', ['', '0000'])
def test_wrong_pin_rejected(pin):
    h, c = client('4321')
    r = c.post('/api/action', json={'action': 'unlock', 'pin': pin})
    assert r.status_code == 403 and h.arduino.commands == []
    assert 'pin_rejected' in h.kinds()


def test_correct_pin_accepted():
    h, c = client('4321')
    r = c.post('/api/action', json={'action': 'unlock', 'pin': '4321'})
    assert r.status_code == 200 and h.arduino.commands == ['UNLOCK']


def test_lock_pauses_auto_and_auto_resumes():
    h, c = client()
    c.post('/api/action', json={'action': 'lock'})
    assert h.system.state['auto'] is False and h.arduino.commands == ['LOCK']
    c.post('/api/action', json={'action': 'auto'})
    assert h.system.state['auto'] is True


def test_invalid_action():
    _, c = client()
    assert c.post('/api/action', json={'action': 'explode'}).status_code == 400


# ==========================================
# PIN 防暴力嘗試
# ==========================================
def test_pin_lockout_after_five_failures():
    h, c = client('4321')
    codes = [c.post('/api/action', json={'action': 'unlock', 'pin': '0000'}).status_code for _ in range(5)]
    assert codes == [403, 403, 403, 403, 429]                # 第 5 次錯誤就開始鎖定
    r = c.post('/api/action', json={'action': 'unlock', 'pin': '4321'})      # 鎖定中，正確 PIN 也不行
    assert r.status_code == 429 and h.arduino.commands == []


def test_pin_guard_unlocks_after_timeout():
    from smartlock.web.server import LOCKOUT_SECONDS, PinGuard
    t = {'now': 0.0}
    g = PinGuard('4321', clock=lambda: t['now'])
    for _ in range(5):
        g.check('ip', 'x')
    assert not g.check('ip', '4321')
    t['now'] += LOCKOUT_SECONDS + 1
    assert g.check('ip', '4321')
    assert g.check('other-ip', '4321')                     # 不同來源不受影響


# ==========================================
# 哨兵紀錄頁
# ==========================================
import numpy as np                                         # noqa: E402
from smartlock.storage import MB, Storage                  # noqa: E402


def records_client(tmp_path, pin='4321'):
    import cv2
    h = Harness(Config(base=tmp_path, control_pin=pin))
    st = Storage(h.cfg, disk_usage=lambda p: (100_000 * MB, 0, 50_000 * MB))
    rid = st.start_record(1_800_000_000, 'clips/a.avi', True)
    w = cv2.VideoWriter(str(st.path('clips/a.avi')), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
    for i in range(5):
        w.write(np.full((120, 160, 3), i * 40, np.uint8))
    w.release()
    cv2.imwrite(str(st.path('clips/a.jpg')), np.zeros((120, 160, 3), np.uint8))
    st.finish_record(rid, 0.5, 'clips/a.jpg', 95.0)
    return h, st, rid, create_web(h.system, st).test_client()


def test_records_require_login(tmp_path):
    _, _, rid, c = records_client(tmp_path)
    for url in ('/api/records', '/api/events', f'/records/{rid}/snapshot.jpg', f'/records/{rid}/play',
                f'/records/{rid}/download'):
        assert c.get(url).status_code == 401, url
    assert c.delete(f'/api/records/{rid}').status_code == 401


def test_records_after_login(tmp_path):
    h, st, rid, c = records_client(tmp_path)
    assert c.post('/api/login', json={'pin': '0000'}).status_code == 403
    assert c.post('/api/login', json={'pin': '4321'}).get_json()['success']
    d = c.get('/api/records').get_json()
    assert d['records'][0]['id'] == rid and d['usage']['count'] == 1
    assert c.get(f'/records/{rid}/snapshot.jpg').data[:2] == b'\xff\xd8'
    assert c.get(f'/records/{rid}/download').data[:4] == b'RIFF'
    play = c.get(f'/records/{rid}/play?speed=4')
    assert play.data.count(b'--frame') == 5                 # 5 幀全部轉成 MJPEG
    h.system.door('UNLOCK', '手動解鎖')
    st.log_event(h.events[-1])
    assert c.get('/api/events?kinds=unlock').get_json()['events'][0]['kind'] == 'unlock'
    assert c.delete(f'/api/records/{rid}').get_json()['success']
    assert c.get('/api/records').get_json()['records'] == []
    assert not st.path('clips/a.avi').exists()


def test_records_open_without_pin(tmp_path):
    _, _, rid, c = records_client(tmp_path, pin='')
    assert c.get('/api/session').get_json() == {'required': False, 'logged_in': True, 'sentinel': True}
    assert c.get('/api/records').status_code == 200


def test_records_page_served(tmp_path):
    _, _, _, c = records_client(tmp_path)
    assert '哨兵紀錄' in c.get('/records').get_data(as_text=True)
    assert c.get('/static/records.js').status_code == 200
