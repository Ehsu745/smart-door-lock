"""每個主人綁定自己的開鎖手勢（gestures.json）。

格式：{"eric": "Victory", "yang": "Thumb_Down"}
- 有綁定：只能用那一個手勢開鎖
- 沒綁定：沿用 ALLOWED_GESTURES（預設讚或 YA），升級後不會有人突然開不了門
- 檔案被別的程式改過（例如在 Pi 上執行 set-gesture）會自動重新讀取，不用重啟

綁定的手勢等於第二道密碼：門口螢幕、網頁狀態都不會顯示「該比哪個手勢」。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Optional

from .config import GESTURE_LABEL

log = logging.getLogger(__name__)

GESTURES = tuple(GESTURE_LABEL)            # MediaPipe 內建的 7 種（不含 None）


class GestureBindings:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: Dict[str, str] = {}
        self._mtime = None
        self._lock = threading.Lock()
        self._refresh()

    def _refresh(self):
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            with self._lock:
                self._data, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            data = {str(k): v for k, v in data.items() if v in GESTURES}
        except (OSError, ValueError, AttributeError):
            log.error('%s 無法讀取，暫時當作沒有綁定手勢', self.path)
            return
        with self._lock:
            self._data, self._mtime = data, mtime

    def get(self, name: str) -> Optional[str]:
        self._refresh()
        with self._lock:
            return self._data.get(name)

    def all(self) -> Dict[str, str]:
        self._refresh()
        with self._lock:
            return dict(self._data)

    def set(self, name: str, gesture: Optional[str]) -> None:
        """gesture=None 代表取消綁定（回到預設的讚 / YA）。"""
        if gesture is not None and gesture not in GESTURES:
            raise ValueError(f'手勢只能是：{", ".join(GESTURES)}')
        self._refresh()
        with self._lock:
            data = dict(self._data)
            if gesture is None:
                data.pop(name, None)
            else:
                data[name] = gesture
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            self._data, self._mtime = data, self.path.stat().st_mtime
