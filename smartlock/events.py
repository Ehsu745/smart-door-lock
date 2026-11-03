"""事件匯流排：DoorSystem 只負責「發出事件」，紀錄、網頁、之後的哨兵與通知各自訂閱。

事件種類（kind）
  seen             看到某人（名字改變時才發）
  face_passed      master 人臉通過，開始等手勢
  gesture_cancel   等手勢時人離開
  gesture_timeout  手勢逾時
  guest_detected   偵測到訪客（data: name）
  unlock / lock    開鎖、上鎖成功（data: reason）
  door_failed      送指令失敗（data: command, message）
  auto_resumed     恢復自動開鎖
  pin_rejected     網頁 PIN 錯誤（data: remote）
  info             其他訊息
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List

log = logging.getLogger(__name__)


@dataclass
class Event:
    kind: str
    text: str = ''
    data: dict = field(default_factory=dict)
    time: str = field(default_factory=lambda: time.strftime('%H:%M:%S'))


Handler = Callable[[Event], None]


class EventBus:
    """同步的發布／訂閱。subscribe('*', handler) 會收到所有事件。單一 handler 出錯不影響其他 handler。"""

    def __init__(self):
        self._subs: Dict[str, List[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, kind: str, handler: Handler) -> None:
        with self._lock:
            self._subs[kind].append(handler)

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = list(self._subs.get(event.kind, ())) + list(self._subs.get('*', ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception('事件處理失敗：%s', event.kind)


class EventLog:
    """保留最近 N 筆事件文字，給網頁顯示。"""

    def __init__(self, maxlen: int = 12):
        self._items = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def __call__(self, event: Event) -> None:
        if event.text:
            with self._lock:
                self._items.appendleft(f'{event.time} {event.text}')

    def recent(self) -> List[str]:
        with self._lock:
            return list(self._items)
