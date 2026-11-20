"""HTTPS：自簽憑證、同一個埠接 http / https、http 轉 https。需要系統有 openssl（Mac、Pi 都有）。"""
import http.client
import shutil
import socket
import ssl
import threading

import pytest

from smartlock.config import Config
from smartlock.tls import _san, cert_fingerprint, cert_valid, make_server, redirect_http, server_context

needs_openssl = pytest.mark.skipif(not shutil.which('openssl'), reason='沒有 openssl')


def test_san_mixes_dns_and_ip():
    assert _san(['pi.local', 'localhost', '127.0.0.1', None, 'pi.local', '::1']) == \
        'DNS:pi.local, DNS:localhost, IP:127.0.0.1, IP:::1'


def test_https_disabled_returns_none(tmp_path):
    assert server_context(Config(base=tmp_path, web_https=False)) is None
    assert not (tmp_path / 'certs').exists()


@needs_openssl
def test_creates_cert_once_and_reuses(tmp_path):
    cfg = Config(base=tmp_path)
    assert isinstance(server_context(cfg, '192.168.0.50'), ssl.SSLContext)
    assert cert_valid(cfg.cert_file) and oct(cfg.key_file.stat().st_mode & 0o777) == '0o600'
    first = cfg.cert_file.read_bytes()
    assert server_context(cfg) is not None and cfg.cert_file.read_bytes() == first     # 手機不用重新信任
    assert len(cert_fingerprint(cfg.cert_file).split(':')) == 8


def test_missing_custom_cert_falls_back_to_http(tmp_path):
    cfg = Config(base=tmp_path, tls_cert=tmp_path / 'nope.crt', tls_key=tmp_path / 'nope.key')
    assert server_context(cfg) is None


def _call(app, **environ):
    env = {'wsgi.url_scheme': 'http', 'REMOTE_ADDR': '192.168.0.9', 'HTTP_HOST': '192.168.0.50:5050',
           'PATH_INFO': '/', 'QUERY_STRING': ''}
    env.update(environ)
    out = {}
    body = app(env, lambda status, headers: out.update(status=status, headers=dict(headers)))
    return out, b''.join(body)


def test_redirect_lan_http_to_https():
    app = redirect_http(lambda env, sr: (sr('200 OK', []), [b'ok'])[1])
    out, _ = _call(app, PATH_INFO='/records', QUERY_STRING='a=1')
    assert out['status'].startswith('307') and out['headers']['Location'] == 'https://192.168.0.50:5050/records?a=1'
    out, body = _call(app, REMOTE_ADDR='127.0.0.1')                   # 本機 curl 排查不轉
    assert out['status'] == '200 OK' and body == b'ok'
    out, body = _call(app, **{'wsgi.url_scheme': 'https'})
    assert body == b'ok'


@pytest.fixture
def live(tmp_path):
    """真的開一個伺服器（隨機埠），https 和 http 都打打看。"""
    from werkzeug.serving import WSGIRequestHandler

    def app(env, sr):
        sr('200 OK', [('Content-Type', 'text/plain')])
        return [env['wsgi.url_scheme'].encode()]

    class Handler(WSGIRequestHandler):
        timeout = 5

    ctx = server_context(Config(base=tmp_path))
    server = make_server('127.0.0.1', 0, app, Handler, ctx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@needs_openssl
def test_same_port_serves_https_and_http(live):
    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    c = http.client.HTTPSConnection('127.0.0.1', live, context=client_ctx, timeout=5)
    c.request('GET', '/')
    assert c.getresponse().read() == b'https'
    c.request('GET', '/')                                               # keep-alive：同一條連線第二個請求
    assert c.getresponse().read() == b'https'
    c.close()
    c = http.client.HTTPConnection('127.0.0.1', live, timeout=5)
    c.request('GET', '/')
    assert c.getresponse().read() == b'http'                           # 本機 http 照常
    c.close()


@needs_openssl
def test_rejected_cert_does_not_break_server(live):
    strict = ssl.create_default_context()                              # 會驗證憑證 → 握手失敗（像手機第一次）
    with pytest.raises(ssl.SSLError):
        with socket.create_connection(('127.0.0.1', live), timeout=5) as s:
            strict.wrap_socket(s, server_hostname='localhost')
    with socket.create_connection(('127.0.0.1', live), timeout=5):
        pass                                                           # 連上就斷（瀏覽器預先連線）
    c = http.client.HTTPConnection('127.0.0.1', live, timeout=5)
    c.request('GET', '/')
    assert c.getresponse().status == 200


def test_config_https_env_and_url(tmp_path):
    from smartlock.qr import control_url
    c = Config.from_env({}, base=tmp_path)
    assert c.web_https and c.cert_file == tmp_path / 'certs' / 'smartlock.crt' and c.talk_max_seconds == 120
    assert control_url(c).startswith('https://') and control_url(c).endswith(':5050')
    c = Config.from_env({'WEB_HTTPS': '0', 'TLS_CERT': '/x/a.crt', 'TLS_KEY': '/x/a.key', 'TALK_MAX_SECONDS': '30'})
    assert not c.web_https and str(c.cert_file) == '/x/a.crt' and str(c.key_file) == '/x/a.key'
    assert c.talk_max_seconds == 30 and control_url(c).startswith('http://')
    assert control_url(Config(public_url='https://door.example')) == 'https://door.example'
