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
    for f in ('app.js', 'app.css', 'qrcode.min.js'):
        assert c.get(f'/static/{f}').status_code == 200, f


def test_status_fields():
    _, c = client()
    d = c.get('/api/status').get_json()
    for key in ('name', 'phase', 'auto', 'events', 'arduino', 'connected', 'pin_required', 'unlocked', 'allowed',
                'unlock_left', 'unlock_seconds'):
        assert key in d


def test_unlock_without_pin():
    h, c = client()
    r = c.post('/api/action', json={'action': 'unlock'})
    assert r.status_code == 200 and h.arduino.commands == ['UNLOCK']


def test_wrong_pin_rejected():
    h, c = client('4321')
    r = c.post('/api/action', json={'action': 'unlock', 'pin': '0000'})
    assert r.status_code == 403 and h.arduino.commands == []
    assert 'pin_rejected' in h.kinds()


def test_missing_pin_asks_login_without_counting_failure():
    h, c = client('4321')
    for _ in range(6):
        assert c.post('/api/action', json={'action': 'unlock'}).status_code == 401
    assert 'pin_rejected' not in h.kinds()
    assert c.post('/api/action', json={'action': 'unlock', 'pin': '4321'}).status_code == 200   # 沒被鎖定


def test_correct_pin_accepted():
    h, c = client('4321')
    r = c.post('/api/action', json={'action': 'unlock', 'pin': '4321'})
    assert r.status_code == 200 and h.arduino.commands == ['UNLOCK']


def test_lock_and_auto_toggle_are_separate():
    h, c = client()
    c.post('/api/action', json={'action': 'lock'})
    assert h.system.state['auto'] is True and h.arduino.commands == ['LOCK']      # 上鎖不再關掉自動解鎖
    c.post('/api/action', json={'action': 'auto_off'})
    assert h.system.state['auto'] is False and h.arduino.commands == ['LOCK']
    c.post('/api/action', json={'action': 'auto'})                                  # 舊名稱仍可用
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


def test_old_records_url_redirects_to_timeline(tmp_path):
    _, _, _, c = records_client(tmp_path)
    r = c.get('/records')
    assert r.status_code == 302 and r.headers['Location'].endswith('/#history')


# ==========================================
# 登入 session：輸入一次 PIN 之後開鎖不用再附 PIN
# ==========================================
def test_session_login_allows_actions_without_pin():
    h, c = client('4321')
    assert c.post('/api/action', json={'action': 'unlock'}).status_code == 401
    assert c.post('/api/login', json={'pin': '4321'}).get_json()['success']
    assert c.post('/api/action', json={'action': 'unlock'}).status_code == 200
    c.post('/api/logout')
    assert c.post('/api/action', json={'action': 'unlock'}).status_code == 401
    assert h.arduino.commands == ['UNLOCK']


def test_action_with_pin_also_logs_in():
    h, c = client('4321')
    assert c.post('/api/action', json={'action': 'lock', 'pin': '4321'}).status_code == 200
    assert c.get('/api/session').get_json()['logged_in']


# ==========================================
# 變更 PIN
# ==========================================
def test_change_pin_flow(tmp_path):
    h = Harness(Config(base=tmp_path, control_pin='1111'))
    app = create_web(h.system)
    me, other = app.test_client(), app.test_client()
    other.post('/api/login', json={'pin': '1111'})
    assert me.post('/api/pin', json={'old': '0000', 'new': '2222'}).status_code == 403
    assert me.post('/api/pin', json={'old': '1111', 'new': '12a'}).status_code == 400
    r = me.post('/api/pin', json={'old': '1111', 'new': '246810'})
    assert r.get_json()['success']
    assert me.get('/api/session').get_json()['logged_in']           # 自己保持登入
    assert not other.get('/api/session').get_json()['logged_in']    # 其他手機要重新輸入
    assert other.post('/api/login', json={'pin': '1111'}).status_code == 403
    assert other.post('/api/login', json={'pin': '246810'}).status_code == 200
    assert 'pin_changed' in h.kinds()
    assert '246810' not in (tmp_path / 'pin.json').read_text()       # 不存明碼


def test_set_first_pin_when_none(tmp_path):
    h = Harness(Config(base=tmp_path))
    c = create_web(h.system).test_client()
    assert c.post('/api/pin', json={'new': '5555'}).get_json()['success']
    assert h.system.snapshot()['pin_required']


# ==========================================
# 成員、設定、時間軸
# ==========================================
def test_users_and_photo(tmp_path):
    import cv2
    h = Harness(Config(base=tmp_path))
    h.recognizer.mapping = {0: ('eric', 'master'), 1: ('amy', 'guest')}
    (tmp_path / 'faces').mkdir()
    for i in range(3):
        cv2.imwrite(str(tmp_path / 'faces' / f'eric_{i}.jpg'), np.zeros((50, 50), np.uint8))
    st = Storage(h.cfg)
    h.system.bus.subscribe('*', st.log_event)
    h.system.emit('seen', '看到 eric master', name='eric', category='master')
    c = create_web(h.system, st).test_client()
    users = {u['name']: u for u in c.get('/api/users').get_json()['users']}
    assert users['eric']['role'] == 'master' and users['eric']['photos'] == 3 and users['eric']['last_seen']
    assert users['amy']['role'] == 'guest' and users['amy']['last_seen'] is None
    assert c.get('/api/users/eric/photo').data[:2] == b'\xff\xd8'
    assert c.get('/api/users/amy/photo').status_code == 404
    assert c.get('/api/users/..%2fface/photo').status_code == 404


def test_users_need_login(tmp_path):
    h = Harness(Config(base=tmp_path, control_pin='4321'))
    c = create_web(h.system).test_client()
    for url in ('/api/users', '/api/settings'):
        assert c.get(url).status_code == 401


def test_settings(tmp_path):
    from smartlock.line import LineClient
    h = Harness(Config(base=tmp_path, line_basic_id='@your_line_id'))
    st = Storage(h.cfg, disk_usage=lambda p: (100_000 * MB, 0, 50_000 * MB))
    h.system.info = {'camera': 'Pi 相機（rpicam）'}
    c = create_web(h.system, st, None).test_client()
    d = c.get('/api/settings').get_json()
    assert d['line'] == {'configured': False, 'basic_id': '@your_line_id', 'target': 'broadcast'}
    assert d['storage']['limit_mb'] == 2048 and d['system']['camera'] == 'Pi 相機（rpicam）'
    assert d['security'] == {'pin_required': False, 'source': 'none'}


def test_events_include_clip(tmp_path):
    h, st, rid, c = records_client(tmp_path)
    c.post('/api/login', json={'pin': '4321'})
    from smartlock.events import Event
    st.log_event(Event('sentinel_saved', f'錄影 #{rid} 已儲存', {'record_id': rid, 'duration': 0.5}))
    st.log_event(Event('sentinel_saved', '錄影 #99 已儲存', {'record_id': 99, 'duration': 3}))
    ev = c.get('/api/events?kinds=sentinel_saved').get_json()['events']
    assert ev[0]['clip'] == {'id': 99, 'deleted': True}
    assert ev[1]['clip']['id'] == rid and ev[1]['clip']['snapshot'] is True
