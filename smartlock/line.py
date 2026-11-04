"""LINE Messaging API：查詢額度（LineClient.quota）、發送文字通知（LineClient.send + LineNotifier）。

token 從 smartlock.env 的 LINE_CHANNEL_TOKEN 讀取，不要寫進程式或推上 GitHub。
查詢額度不算訊息則數；結果快取 10 分鐘，網頁每秒輪詢也不會一直打 LINE。
有 LINE_USER_ID 就 push 給那個人，沒有就 broadcast 給所有好友（每位好友各算一則）。
通知內容只有文字，不放開鎖連結。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
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

    # ==========================================
    # 發送
    # ==========================================
    def send(self, text: str, user_id: str = '') -> None:
        """發一則文字訊息。有 user_id 用 push，沒有用 broadcast。失敗丟 LineError（訊息可直接顯示）。
        網路錯誤或 5xx 會用同一個 X-Line-Retry-Key 重試一次，LINE 端不會重複發送。"""
        if not self.configured:
            raise LineError('還沒設定 LINE_CHANNEL_TOKEN')
        path = '/message/push' if user_id else '/message/broadcast'
        body = {'messages': [{'type': 'text', 'text': text[:5000]}]}
        if user_id:
            body['to'] = user_id
        headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json',
                   'X-Line-Retry-Key': str(uuid.uuid4())}
        data = json.dumps(body, ensure_ascii=False).encode()
        for attempt in (1, 2):
            req = urllib.request.Request(API + path, data=data, headers=headers, method='POST')
            try:
                with self.opener(req, timeout=10) as resp:
                    resp.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 409:              # 同一個 retry key 已經送過 → 第一次其實成功了
                    break
                if exc.code >= 500 and attempt == 1:
                    continue
                raise LineError(_http_message(exc)) from exc
            except (urllib.error.URLError, OSError) as exc:
                if attempt == 1:
                    continue
                raise LineError(f'連不到 LINE（{getattr(exc, "reason", exc)}）') from exc
        if user_id:
            self.count_sent(1)
        else:
            self.invalidate()                    # 廣播的用量 = 好友數，不知道確切值 → 下次重新查

    def invalidate(self) -> None:
        with self._lock:
            self._until = 0.0


class LineError(Exception):
    pass


def _http_message(exc: urllib.error.HTTPError) -> str:
    if exc.code == 401:
        return 'token 無效，請到 LINE Developers 重新發行'
    if exc.code == 429:
        return '本月訊息額度用完了（或發送太頻繁）'
    try:
        detail = json.loads(exc.read().decode() or '{}').get('message', '')
    except (ValueError, OSError):
        detail = ''
    return f'LINE 回應 {exc.code}' + (f'：{detail}' if detail else '')


# ==========================================
# 通知：訂閱事件，在背景執行緒發送
# ==========================================
IMPORTANT = ('door_failed', 'guest_detected')    # 額度快用完時只發這些
LOW_QUOTA = 20


def format_event(kind: str, data: dict, when: str) -> Optional[str]:
    """事件 → 通知文字。回傳 None 表示這個事件不通知。內容不放開鎖連結。"""
    hm = when[:5]
    if kind == 'stranger_detected':
        if not data.get('notify'):
            return None                          # 哨兵已做冷卻：只有冷卻過後的第一段錄影才通知
        rid = data.get('record_id')
        return f'【智慧門鎖】{hm} 有陌生人在門口逗留，已開始錄影' + (f'（#{rid}）' if rid else '') + '。可到手機控制頁的「紀錄」查看。'
    if kind == 'guest_detected':
        return f'【智慧門鎖】{hm} 訪客 {data.get("name", "")} 在門口。要開門請到手機控制頁操作。'
    if kind == 'door_failed':
        cmd = {'UNLOCK': '開鎖', 'LOCK': '上鎖'}.get(str(data.get('command', '')).upper(), data.get('command', ''))
        return f'【智慧門鎖】{hm} {cmd}指令失敗：{data.get("message", "")}。請檢查 Arduino 連線。'
    return None


class LineNotifier:
    """訂閱 stranger_detected / guest_detected / door_failed，排進佇列由背景執行緒發送。

    - 不會卡住辨識迴圈：publish 只做 put_nowait，佇列滿了就丟掉並記錄
    - 同一種通知（訪客以名字區分）cooldown 秒內只發一次；陌生人的冷卻由哨兵負責
    - 額度剩 LOW_QUOTA 以下只發 IMPORTANT；用完就全部不發
    - 發送失敗只記錄，不會讓系統掛掉
    """
    KINDS = ('stranger_detected', 'guest_detected', 'door_failed')

    def __init__(self, client: LineClient, bus=None, user_id: str = '', cooldown: float = 300.0,
                 clock=time.monotonic, start: bool = True):
        self.client = client
        self.user_id = user_id
        self.cooldown = cooldown
        self.clock = clock
        self.queue: queue.Queue = queue.Queue(maxsize=20)
        self._last = {}
        self._lock = threading.Lock()
        self.sent = 0
        self.last_sent = None                    # (時間字串, 文字)
        self.last_error = None
        if bus is not None:
            for kind in self.KINDS:
                bus.subscribe(kind, self.on_event)
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, daemon=True, name='line-notify')
            self._thread.start()

    def on_event(self, event) -> None:
        text = format_event(event.kind, event.data or {}, event.time)
        if text is None:
            return
        key = (event.kind, (event.data or {}).get('name')) if event.kind != 'stranger_detected' else None
        now = self.clock()
        with self._lock:
            if key is not None and key in self._last and now - self._last[key] < self.cooldown:
                log.info('LINE 通知冷卻中，略過：%s', text)
                return
            if key is not None:
                self._last[key] = now
        try:
            self.queue.put_nowait((event.kind, text))
        except queue.Full:
            log.warning('LINE 通知佇列已滿，略過：%s', text)

    def process(self, kind: str, text: str) -> bool:
        """實際發送一則（背景執行緒呼叫；測試可直接呼叫）。回傳是否送出。"""
        q = self.client.quota()
        left = q.get('left')
        if left is not None:
            if left <= 0:
                self.last_error = '本月額度已用完，通知暫停'
                log.warning('LINE 額度用完，略過：%s', text)
                return False
            if left <= LOW_QUOTA and kind not in IMPORTANT:
                log.info('LINE 額度剩 %d，只發重要通知，略過：%s', left, text)
                return False
        try:
            self.client.send(text, self.user_id)
        except LineError as exc:
            self.last_error = str(exc)
            log.warning('LINE 通知失敗：%s（%s）', exc, text)
            return False
        except Exception as exc:                 # 任何意外都不能讓背景執行緒結束
            self.last_error = f'發送時發生錯誤：{exc}'
            log.exception('LINE 通知發生未預期錯誤')
            return False
        self.sent += 1
        self.last_sent = (time.strftime('%m/%d %H:%M'), text)
        self.last_error = None
        log.info('LINE 通知已送出：%s', text)
        return True

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:
                return
            self.process(*item)

    def close(self, timeout: float = 5.0) -> None:
        """結束前把佇列裡的通知送完（最多等 timeout 秒）。"""
        if self._thread is not None:
            try:
                self.queue.put(None, timeout=1)
            except queue.Full:
                return
            self._thread.join(timeout)

    def status(self) -> dict:
        return {'sent': self.sent, 'last_sent': self.last_sent[0] if self.last_sent else None,
                'last_text': self.last_sent[1] if self.last_sent else None, 'last_error': self.last_error}
