"""SQLite 紀錄與 SD 卡容量管理。

records 表：哨兵錄影（一段影片一筆）
events  表：門禁事件文字紀錄（開鎖、訪客、PIN 錯誤…），給網頁「事件紀錄」查詢

容量管理（enforce）：依序刪除
  1. 超過 record_max_days 天的錄影
  2. 總容量超過 record_max_mb 時，從最舊的刪起
  3. SD 卡剩餘空間低於 record_min_free_mb 時，從最舊的刪起
錄影中的那一段永遠不會被刪。
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import Config

log = logging.getLogger(__name__)

MB = 1024 * 1024
DB_NAME = 'smartlock.db'
SKIP_EVENTS = {'seen'}                    # 太頻繁、價值低的事件不寫入資料庫

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started      REAL NOT NULL,          -- Unix 時間
    duration     REAL NOT NULL DEFAULT 0,
    video        TEXT,                   -- 相對於 records 資料夾
    snapshot     TEXT,
    min_distance REAL,                   -- 最小 LBPH 距離（越小越像已知的人）
    notified     INTEGER NOT NULL DEFAULT 0,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'recording'    -- recording | done | failed
);
CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    time  REAL NOT NULL,
    kind  TEXT NOT NULL,
    text  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
    name  TEXT PRIMARY KEY,              -- 成員最近一次被認出的時間
    last  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_started ON records(started);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(time);
"""


@dataclass
class Usage:
    count: int
    used_mb: float
    limit_mb: float
    keep_days: float
    disk_free_mb: float
    disk_total_mb: float

    def as_dict(self):
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


class Storage:
    def __init__(self, cfg: Config, wallclock: Callable[[], float] = time.time,
                 disk_usage: Callable[[Path], tuple] = shutil.disk_usage):
        self.cfg = cfg
        self.root = cfg.records_path
        self.clips = self.root / 'clips'
        self.clips.mkdir(parents=True, exist_ok=True)
        self.wallclock = wallclock
        self.disk_usage = disk_usage
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(self.root / DB_NAME), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute('PRAGMA table_info(events)')}
        if 'data' not in cols:                   # v2.1 的資料庫沒有這欄，直接補上
            self.db.execute('ALTER TABLE events ADD COLUMN data TEXT')
        # 上次程式中斷時還在錄的那段：檔案可能不完整，標成 failed（仍可下載）
        self.db.execute("UPDATE records SET status='failed' WHERE status='recording'")
        self.db.commit()

    # ==========================================
    # 錄影紀錄
    # ==========================================
    def start_record(self, started: float, video: str, notified: bool) -> int:
        with self._lock:
            cur = self.db.execute('INSERT INTO records (started, video, notified) VALUES (?, ?, ?)',
                                  (started, video, int(notified)))
            self.db.commit()
            return cur.lastrowid

    def finish_record(self, rid: int, duration: float, snapshot: Optional[str], min_distance: Optional[float],
                      ok: bool = True):
        size = sum(self.path(p).stat().st_size for p in (self.get(rid)['video'], snapshot)
                   if p and self.path(p).exists())
        with self._lock:
            self.db.execute('UPDATE records SET duration=?, snapshot=?, min_distance=?, size_bytes=?, status=? '
                            'WHERE id=?', (round(duration, 1), snapshot, min_distance, size,
                                           'done' if ok else 'failed', rid))
            self.db.commit()

    def get(self, rid: int) -> Optional[dict]:
        with self._lock:
            row = self.db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
        return dict(row) if row else None

    def list_records(self, limit: int = 50, before: Optional[int] = None) -> List[dict]:
        sql, args = 'SELECT * FROM records', []
        if before:
            sql, args = sql + ' WHERE id < ?', [before]
        with self._lock:
            rows = self.db.execute(sql + ' ORDER BY id DESC LIMIT ?', args + [limit]).fetchall()
        return [dict(r) for r in rows]

    def delete(self, rid: int) -> bool:
        rec = self.get(rid)
        if not rec or rec['status'] == 'recording':
            return False
        for p in (rec['video'], rec['snapshot']):
            if p:
                try:
                    self.path(p).unlink()
                except FileNotFoundError:
                    pass
        with self._lock:
            self.db.execute('DELETE FROM records WHERE id=?', (rid,))
            self.db.commit()
        return True

    def path(self, rel: str) -> Path:
        """把資料庫裡的相對路徑轉成實際路徑，並確認沒有跑出 records 資料夾。"""
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f'路徑超出紀錄資料夾：{rel}')
        return p

    # ==========================================
    # 事件紀錄
    # ==========================================
    def log_event(self, event) -> None:
        """訂閱 EventBus 用：bus.subscribe('*', storage.log_event)。"""
        if event.kind == 'seen' and event.data.get('category') in ('master', 'guest'):
            self.touch(event.data['name'])
        if event.kind in SKIP_EVENTS or not event.text:
            return
        data = json.dumps(event.data, ensure_ascii=False) if event.data else None
        with self._lock:
            self.db.execute('INSERT INTO events (time, kind, text, data) VALUES (?, ?, ?, ?)',
                            (self.wallclock(), event.kind, event.text, data))
            self.db.commit()

    def touch(self, name: str) -> None:
        with self._lock:
            self.db.execute('INSERT INTO presence (name, last) VALUES (?, ?) '
                            'ON CONFLICT(name) DO UPDATE SET last=excluded.last', (name, self.wallclock()))
            self.db.commit()

    def last_seen(self) -> Dict[str, float]:
        with self._lock:
            return {r['name']: r['last'] for r in self.db.execute('SELECT name, last FROM presence')}

    def list_events(self, limit: int = 100, kinds: Optional[List[str]] = None,
                    before: Optional[int] = None) -> List[dict]:
        where, args = [], []
        if kinds:
            where.append(f'kind IN ({",".join("?" * len(kinds))})')
            args += list(kinds)
        if before:
            where.append('id < ?')
            args.append(before)
        sql = 'SELECT * FROM events' + (' WHERE ' + ' AND '.join(where) if where else '')
        with self._lock:
            rows = self.db.execute(sql + ' ORDER BY id DESC LIMIT ?', args + [limit]).fetchall()
        out = []
        for r in rows:
            e = dict(r)
            e['data'] = json.loads(e['data']) if e.get('data') else {}
            out.append(e)
        return out

    # ==========================================
    # 容量管理
    # ==========================================
    def usage(self) -> Usage:
        with self._lock:
            count, used = self.db.execute('SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM records').fetchone()
        total, _, free = self.disk_usage(self.root)
        return Usage(count, used / MB, self.cfg.record_max_mb, self.cfg.record_max_days, free / MB, total / MB)

    def disk_ok(self) -> bool:
        return self.disk_usage(self.root)[2] / MB >= self.cfg.record_min_free_mb

    def _oldest(self):
        with self._lock:
            row = self.db.execute("SELECT id, started FROM records WHERE status != 'recording' "
                                  'ORDER BY started LIMIT 1').fetchone()
        return (row['id'], row['started']) if row else (None, None)

    def enforce(self) -> int:
        """執行容量規則，回傳刪除的段數。"""
        cfg, removed = self.cfg, 0
        cutoff = self.wallclock() - cfg.record_max_days * 86400
        while True:
            rid, started = self._oldest()
            if rid is None:
                break
            u = self.usage()
            too_old = started < cutoff
            too_big = u.used_mb > cfg.record_max_mb
            disk_low = u.disk_free_mb < cfg.record_min_free_mb
            if not (too_old or too_big or disk_low):
                break
            self.delete(rid)
            removed += 1
            log.info('自動刪除錄影 #%d（%s）', rid, '超過保留天數' if too_old else '容量上限' if too_big else 'SD 卡空間不足')
        with self._lock:
            self.db.execute('DELETE FROM events WHERE time < ?', (self.wallclock() - cfg.event_keep_days * 86400,))
            self.db.commit()
        return removed

    def close(self):
        with self._lock:
            self.db.close()
