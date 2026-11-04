"""LINE Messaging API（目前只有查詢額度；發送通知會在下一步加上）。

token 從 smartlock.env 的 LINE_CHANNEL_TOKEN 讀取，不要寫進程式或推上 GitHub。
查詢額度不算訊息則數；結果快取 10 分鐘，網頁每秒輪詢也不會一直打 LINE。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Callable, Optional

log = logging.getLogger(__name__)

API = 'https://api.line.me/v2/bot'
CACHE_SECONDS = 600
ERROR_CACHE_SECONDS = 60


def next_reset(today: Optional[date] = None) -> str:
    today = today or date.today()
    month = today.month % 12 + 1
    return f'{month}/1'


class LineClient:
    def __init__(self, token: str, opener: Callable = urllib.request.urlopen, clock=time.monotonic):
        self.token = token
        self.opener = opener
        self.clock = clock
        self._cache = None
        self._until = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(API + path, headers={'Authorization': f'Bearer {self.token}'})
        with self.opener(req, timeout=5) as resp:
            return json.loads(resp.read().decode() or '{}')

    def quota(self, refresh: bool = False) -> dict:
        """回傳 {configured, limit, used, left, reset, error}。"""
        if not self.configured:
            return {'configured': False}
        with self._lock:
            if not refresh and self._cache is not None and self.clock() < self._until:
                return self._cache
        try:
            q = self._get('/message/quota')
            used = self._get('/message/quota/consumption').get('totalUsage', 0)
            limit = q.get('value') if q.get('type') == 'limited' else None
            data = {'configured': True, 'limit': limit, 'used': used,
                    'left': None if limit is None else max(0, limit - used), 'reset': next_reset(), 'error': None}
            ttl = CACHE_SECONDS
        except urllib.error.HTTPError as exc:
            msg = 'token 無效，請到 LINE Developers 重新發行' if exc.code == 401 else f'LINE 回應 {exc.code}'
            data, ttl = {'configured': True, 'error': msg}, ERROR_CACHE_SECONDS
        except (urllib.error.URLError, OSError, ValueError) as exc:
            data, ttl = {'configured': True, 'error': f'連不到 LINE（{getattr(exc, "reason", exc)}）'}, ERROR_CACHE_SECONDS
        with self._lock:
            self._cache, self._until = data, self.clock() + ttl
        return data

    def count_sent(self, n: int = 1) -> None:
        """發出通知後先在本地加上用量，網頁上的數字不用等下次查詢。"""
        with self._lock:
            if self._cache and self._cache.get('used') is not None:
                self._cache = dict(self._cache, used=self._cache['used'] + n)
                if self._cache.get('limit') is not None:
                    self._cache['left'] = max(0, self._cache['limit'] - self._cache['used'])
