"""PIN 管理與設定檔載入。

PIN 的來源（優先順序）
  1. pin.json   ：網頁或 `python app.py set-pin` 設定過的 PIN，只存 PBKDF2 雜湊，不存明碼
  2. CONTROL_PIN：環境變數 / smartlock.env（舊做法，仍然支援）
  都沒有 = 不需要 PIN（任何人都能從網頁開門，啟動時會警告）

改 PIN 後 version 加 1，網頁上用舊 PIN 登入的 session 會自動失效。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

PIN_FILE = 'pin.json'
ENV_FILE = 'smartlock.env'
ITERATIONS = 120_000                 # Pi 5 上驗證一次約 0.1 秒：使用者無感，暴力猜測變慢
PIN_RULE = re.compile(r'\d{4,8}')


def _hash(pin: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, iterations).hex()


class PinStore:
    def __init__(self, path: Path, env_pin: str = ''):
        self.path = Path(path)
        self.env_pin = env_pin
        self._lock = threading.Lock()
        self._data: Optional[dict] = None
        self._mtime = None
        self._refresh()
        if self._data and env_pin:
            log.info('PIN 以 %s 為準（smartlock.env 的 CONTROL_PIN 不再使用）', self.path.name)

    def _refresh(self):
        """pin.json 被別的程式改過（例如在 Pi 上執行 set-pin）就重新讀，正在跑的系統不用重啟。"""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            log.error('%s 無法讀取，改用 CONTROL_PIN', self.path)
            return
        with self._lock:
            self._data, self._mtime = data, mtime

    @property
    def required(self) -> bool:
        self._refresh()
        return bool(self._data or self.env_pin)

    @property
    def source(self) -> str:
        return 'file' if self._data else 'env' if self.env_pin else 'none'

    @property
    def version(self) -> int:
        self._refresh()
        return self._data['version'] if self._data else 0

    def verify(self, pin) -> bool:
        self._refresh()
        pin = str(pin or '')
        with self._lock:
            data = self._data
        if data:
            got = _hash(pin, bytes.fromhex(data['salt']), data['iterations'])
            return hmac.compare_digest(got, data['hash'])
        if self.env_pin:
            return hmac.compare_digest(pin, self.env_pin)
        return True

    def set(self, new_pin: str) -> None:
        if not PIN_RULE.fullmatch(new_pin or ''):
            raise ValueError('PIN 要是 4 到 8 位數字')
        salt = secrets.token_bytes(16)
        data = {'hash': _hash(new_pin, salt, ITERATIONS), 'salt': salt.hex(),
                'iterations': ITERATIONS, 'version': self.version + 1}
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)                     # 只有 Pi 上的使用者讀得到
        tmp.replace(self.path)                   # 原子替換：寫到一半斷電也不會壞掉
        with self._lock:
            self._data, self._mtime = data, self.path.stat().st_mtime


def load_env_file(path: Path, environ=None) -> Dict[str, str]:
    """讀 KEY=VALUE 格式的設定檔放進環境變數；已經存在的環境變數優先（指令前面加的設定會蓋過檔案）。

    這樣手動 `python app.py run` 和 systemd 背景服務用的是同一份設定。
    """
    environ = os.environ if environ is None else environ
    loaded = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key.startswith('export '):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        elif '#' in value:
            value = value.split('#', 1)[0].rstrip()   # 行尾註解
        if key and key not in environ:
            environ[key] = value
            loaded[key] = value
    return loaded
