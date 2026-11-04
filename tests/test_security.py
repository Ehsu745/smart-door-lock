"""PIN 雜湊儲存、設定檔載入、LINE 額度查詢。"""
import io
import json
import os
import urllib.error

import pytest

from smartlock.line import LineClient, next_reset
from smartlock.security import PinStore, load_env_file


def test_pin_store_env_fallback(tmp_path):
    s = PinStore(tmp_path / 'pin.json', env_pin='1234')
    assert s.required and s.source == 'env' and s.verify('1234') and not s.verify('0000')


def test_pin_store_hash_and_version(tmp_path):
    s = PinStore(tmp_path / 'pin.json', env_pin='1234')
    s.set('98765')
    data = json.loads((tmp_path / 'pin.json').read_text())
    assert '98765' not in json.dumps(data) and data['version'] == 1
    assert s.source == 'file' and s.verify('98765') and not s.verify('1234')    # 檔案優先於 CONTROL_PIN
    assert oct(os.stat(tmp_path / 'pin.json').st_mode)[-3:] == '600'
    s.set('1111')
    assert s.version == 2


@pytest.mark.parametrize('bad', ['123', '123456789', 'abcd', '', '12 34'])
def test_pin_rule(tmp_path, bad):
    with pytest.raises(ValueError):
        PinStore(tmp_path / 'pin.json').set(bad)


def test_pin_store_sees_change_from_cli(tmp_path):
    """Pi 上執行 set-pin 改了 pin.json，正在跑的程式不用重啟就套用。"""
    running = PinStore(tmp_path / 'pin.json')
    assert not running.required
    PinStore(tmp_path / 'pin.json').set('4444')
    assert running.required and running.verify('4444')


def test_no_pin(tmp_path):
    s = PinStore(tmp_path / 'pin.json')
    assert not s.required and s.source == 'none' and s.verify('anything')


def test_load_env_file(tmp_path):
    f = tmp_path / 'smartlock.env'
    f.write_text('# 註解\nCONTROL_PIN=1234\nexport WEB_PORT=6000\nLINE_BASIC_ID="@abc"\n'
                 'SIM_ARDUINO=1   # 行尾註解\n\nBROKEN LINE\nFACE_THRESHOLD=55\n', encoding='utf-8')
    env = {'FACE_THRESHOLD': '70'}                     # 已經存在的環境變數優先
    loaded = load_env_file(f, env)
    assert env == {'FACE_THRESHOLD': '70', 'CONTROL_PIN': '1234', 'WEB_PORT': '6000',
                   'LINE_BASIC_ID': '@abc', 'SIM_ARDUINO': '1'}
    assert 'FACE_THRESHOLD' not in loaded
    assert load_env_file(tmp_path / 'missing.env', {}) == {}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_line_quota_and_cache():
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        assert req.headers['Authorization'] == 'Bearer T'
        body = {'type': 'limited', 'value': 200} if req.full_url.endswith('/quota') else {'totalUsage': 13}
        return FakeResp(json.dumps(body).encode())
    t = {'now': 0}
    c = LineClient('T', opener=opener, clock=lambda: t['now'])
    q = c.quota()
    assert (q['limit'], q['used'], q['left']) == (200, 13, 187) and q['error'] is None
    c.quota()
    assert len(calls) == 2                             # 10 分鐘內用快取
    c.count_sent(3)
    assert c.quota()['left'] == 184
    t['now'] = 601
    c.quota()
    assert len(calls) == 4


def test_line_bad_token():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, None)
    q = LineClient('bad', opener=opener).quota()
    assert q['configured'] and 'token 無效' in q['error']


def test_line_not_configured():
    assert LineClient('').quota() == {'configured': False}


def test_next_reset():
    from datetime import date
    assert next_reset(date(2026, 11, 4)) == '12/1' and next_reset(date(2026, 12, 31)) == '1/1'
