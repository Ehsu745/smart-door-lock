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
