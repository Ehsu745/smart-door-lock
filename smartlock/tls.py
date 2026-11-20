"""HTTPS：自簽憑證 + 同一個埠同時接 http / https。

手機瀏覽器只有在 HTTPS 頁面才允許用麥克風（按住說話）。門鎖在區網裡，沒有網域名稱，所以用自簽憑證：
- 第一次啟動時用系統的 openssl 產生憑證（certs/，不進 git），之後沿用同一張；手機第一次開會跳「連線不安全」，
  按「顯示詳細資訊 → 瀏覽此網站」一次就好（換 IP 時會再問一次）
- 同一個埠（5050）同時接 http 和 https：看連線的第一個 byte，TLS 握手（0x16）就走 https，否則是 http，
  http 一律轉到 https（舊的書籤、QR code、直接打 IP 都能用）；本機（127.0.0.1）的 http 不轉，方便 curl 排查
- 不送 HSTS：自簽憑證加 HSTS 會讓瀏覽器不再允許「仍要前往」
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CERT_DAYS = 825                     # Apple 對 TLS 憑證的上限；超過的話 iPhone 可能直接拒絕連線
TLS_HANDSHAKE = 0x16
LOOPBACK = ('127.0.0.1', '::1', 'localhost')

_OPENSSL_CONF = """[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = smartlock
O = smartlock
[v3]
subjectAltName = {san}
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
"""


def _san(hosts) -> str:
    out = []
    for h in dict.fromkeys(h for h in hosts if h):
        try:
            socket.inet_pton(socket.AF_INET6 if ':' in h else socket.AF_INET, h)
            out.append(f'IP:{h}')
        except OSError:
            out.append(f'DNS:{h}')
    return ', '.join(out)


def cert_hosts(lan_ip=None):
    host = socket.gethostname().split('.')[0]
    return [f'{host}.local', host, 'localhost', '127.0.0.1', lan_ip]


def cert_valid(cert: Path) -> bool:
    """憑證存在、openssl 讀得懂、7 天內不會過期。"""
    if not cert.is_file():
        return False
    try:
        r = subprocess.run(['openssl', 'x509', '-checkend', str(7 * 86400), '-noout', '-in', str(cert)],
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True                        # 沒辦法檢查就照用
    return r.returncode == 0


def create_cert(cert: Path, key: Path, hosts) -> None:
    """用 openssl 產生自簽憑證（RSA 2048、825 天、SAN 含 hostname.local / IP）。失敗丟 RuntimeError。"""
    if not shutil.which('openssl'):
        raise RuntimeError('找不到 openssl（sudo apt install openssl）')
    cert.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / 'openssl.cnf'
        conf.write_text(_OPENSSL_CONF.format(san=_san(hosts)))
        tmp_key, tmp_cert = Path(tmp) / 'k.pem', Path(tmp) / 'c.pem'
        r = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256',
                            '-days', str(CERT_DAYS), '-keyout', str(tmp_key), '-out', str(tmp_cert),
                            '-config', str(conf)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not tmp_cert.is_file():
            raise RuntimeError(f'openssl 產生憑證失敗：{(r.stderr or r.stdout).strip()[-300:]}')
        os.chmod(tmp_key, 0o600)
        shutil.move(str(tmp_key), key)
        shutil.move(str(tmp_cert), cert)


def server_context(cfg, lan_ip=None):
    """回傳 HTTPS 用的 SSLContext；沒有憑證就自動產生。HTTPS 關閉或失敗時回傳 None（改用 http）。"""
    if not cfg.web_https:
        return None
    cert, key = cfg.cert_file, cfg.key_file
    try:
        if not (key.is_file() and cert_valid(cert)):
            if cfg.tls_cert:
                raise RuntimeError(f'TLS_CERT 指定的憑證不存在或已過期：{cert}')
            log.info('產生自簽憑證：%s', cert)
            create_cert(cert, key, cert_hosts(lan_ip))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(cert), str(key))
        return ctx
    except (RuntimeError, OSError, ssl.SSLError, subprocess.SubprocessError) as e:
        log.warning('HTTPS 無法啟用，改用 http（手機不能按住說話）：%s', e)
        return None


def cert_fingerprint(cert: Path) -> str:
    """SHA-256 指紋（前 8 組），讓使用者對照手機上看到的憑證。"""
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(cert).read_text())
    except (OSError, ValueError):
        return ''
    import hashlib
    h = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(h[i:i + 2] for i in range(0, 16, 2))


# ==========================================
# 同一個埠接 http / https 的伺服器
# ==========================================
def redirect_http(app):
    """WSGI 包裝：從區網來的 http 請求轉到 https（同一個 host:port）。本機的 http 照常回應。"""
    def wrapped(environ, start_response):
        if environ.get('wsgi.url_scheme') == 'http' and environ.get('REMOTE_ADDR') not in LOOPBACK:
            host = environ.get('HTTP_HOST') or f"{environ.get('SERVER_NAME')}:{environ.get('SERVER_PORT')}"
            path = environ.get('PATH_INFO', '/')
            if environ.get('QUERY_STRING'):
                path += '?' + environ['QUERY_STRING']
            # 307：瀏覽器不會永久記住（之後關掉 HTTPS 也不會卡住），POST 也會照原樣重送
            start_response('307 Temporary Redirect', [('Location', f'https://{host}{path}'),
                                                       ('Content-Length', '0'), ('Cache-Control', 'no-store')])
            return [b'']
        return app(environ, start_response)
    return wrapped


def make_server(host, port, app, handler, ssl_ctx):
    """ssl_ctx 為 None 時就是一般的 http 伺服器（werkzeug ThreadedWSGIServer）。"""
    from werkzeug.serving import ThreadedWSGIServer

    if ssl_ctx is None:
        return ThreadedWSGIServer(host, port, app, handler)

    class TLSHandler(handler):
        def make_environ(self):
            environ = super().make_environ()
            environ['wsgi.url_scheme'] = 'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'
            return environ

        def handle(self):
            try:
                super().handle()
            except (ssl.SSLError, ssl.SSLEOFError) as e:      # 手機拒絕憑證、中途斷線：常見，不用印 traceback
                log.debug('TLS 連線中斷：%s', e)

    class DualServer(ThreadedWSGIServer):
        def finish_request(self, request, client_address):
            """在處理請求的執行緒裡（不卡住 accept）判斷 http / https，再交給 werkzeug。"""
            request.settimeout(getattr(handler, 'timeout', None) or 20)
            try:
                first = request.recv(1, socket.MSG_PEEK)
            except OSError:
                return
            if not first:
                return
            if first[0] != TLS_HANDSHAKE:
                return super().finish_request(request, client_address)
            try:
                conn = ssl_ctx.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError) as e:
                log.debug('TLS 握手失敗（%s）：%s', client_address[0], e)   # 手機第一次看到自簽憑證時會這樣
                return
            try:
                super().finish_request(conn, client_address)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    return DualServer(host, port, redirect_http(app), TLSHandler)
