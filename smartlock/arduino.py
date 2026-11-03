"""門鎖控制：USB 序列埠送 unlock / lock 給 Arduino（與 Serial Monitor 手動輸入相同）。

兩種實作有相同的介面：connect() / send(command) → (ok, message) / watchdog(stop) / close() / status / connected
"""
from __future__ import annotations

import glob
import logging
import os
import threading
import time

from .config import Config

try:
    import serial
except ImportError:          # 沒裝 pyserial 時仍可採集、訓練
    serial = None

log = logging.getLogger(__name__)

PORT_PATTERNS = ('/dev/serial/by-id/*', '/dev/ttyACM*', '/dev/ttyUSB*',
                 '/dev/cu.usbmodem*', '/dev/cu.usbserial*', '/dev/cu.wchusbserial*')   # 後三個是 macOS


class Arduino:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.port = None
        self.ser = None
        self.lock = threading.RLock()
        self.status = '未連線'
        self._last_logged = None

    def find_port(self):
        if self.cfg.arduino_port:
            return self.cfg.arduino_port
        for pattern in PORT_PATTERNS:
            found = sorted(glob.glob(pattern))
            if found:
                return found[0]
        return None

    @property
    def connected(self) -> bool:
        return self.ser is not None

    def _close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @staticmethod
    def matches(command, line) -> bool:
        """判斷 Arduino 的回覆是否確認了這個指令（學弟的 sketch 回 'Unlocked: ...' / 'Locked'）。"""
        low = line.lower()
        if any(word in low for word in ('unknown', 'error', 'ready', 'invalid')):
            return False                         # 錯誤訊息／開機提示裡也有 unlock 字樣，不能算確認
        if command == 'UNLOCK':
            return 'unlock' in low or '開' in line
        return ('lock' in low and 'unlock' not in low) or '關' in line or '上鎖' in line

    def _exchange(self, command, timeout=1.0):
        """送出指令並收集回覆。回傳 (是否確認, 回覆文字列表)。"""
        self.ser.reset_input_buffer()            # 丟掉 Arduino 先前主動送出的訊息
        self.ser.write((self.cfg.cmd_text(command) + self.cfg.cmd_eol).encode('utf-8'))
        self.ser.flush()
        replies = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.ser.readline().decode('utf-8', errors='replace').strip()
            if not line:
                continue
            replies.append(line)
            if self.matches(command, line):
                return True, replies
        return False, replies

    def connect(self) -> bool:
        with self.lock:
            self._close()
            if serial is None:
                self.status = '缺少 pyserial（pip install pyserial）'
                return False
            port = self.find_port()
            if port is None:
                self.status = '找不到 Arduino（檢查 USB 線是否可傳資料）'
                return False
            try:
                self.ser = serial.Serial(port, self.cfg.baud, timeout=0.2, write_timeout=1)
                time.sleep(2)                    # 開啟序列埠會讓 UNO 重新開機
                confirmed, replies = self._exchange('LOCK')   # 用「上鎖」當握手：最安全的狀態
                if confirmed or not self.cfg.ack_strict:
                    self.port = port
                    note = '' if confirmed else (f'，回覆：{" / ".join(replies)}' if replies else '，Arduino 沒有回覆文字')
                    self.status = f'已連線 {port}{note}'
                    log.info('Arduino %s', self.status)
                    return True
                self.status = (f'{port} 有開啟但 Arduino 沒確認（回覆：{replies or "無"}）：'
                               f'檢查 baud={self.cfg.baud}、指令文字與 Arduino 程式')
            except (serial.SerialException, OSError) as exc:
                self.status = f'連線失敗：{exc}'
            self._close()
            if self.status != self._last_logged:
                log.info('Arduino %s', self.status)
                self._last_logged = self.status
            return False

    def send(self, command):
        """回傳 (成功與否, 訊息)"""
        with self.lock:
            if self.ser is None and not self.connect():
                return False, self.status
            try:
                text = self.cfg.cmd_text(command)
                confirmed, replies = self._exchange(command)
                reply = ' / '.join(replies) if replies else '無回覆'
                if confirmed:
                    return True, f'已送出 {text}，Arduino 回覆：{reply}'
                if not self.cfg.ack_strict:
                    return True, f'已送出 {text}（Arduino 回覆：{reply}）'
                return False, f'已送出 {text}，但 Arduino 未確認（{reply}）'
            except (serial.SerialException, OSError) as exc:
                self._close()
                self.status = f'連線中斷：{exc}'
                return False, self.status

    def watchdog(self, stop):
        """背景執行：USB 拔掉時標示斷線，插回後自動重連。"""
        while not stop.wait(3):
            with self.lock:
                if self.ser is not None and self.port and not os.path.exists(self.port):
                    self._close()
                    self.status = 'USB 已拔除，等待重新連線'
                    log.info(self.status)
                elif self.ser is None:
                    self.connect()

    def close(self):
        with self.lock:
            self._close()


class FakeArduino:
    """模擬模式：不接 Arduino，用文字顯示門鎖動作；行為與 door_lock.ino 相同（解鎖後自動上鎖）。"""

    def __init__(self, cfg: Config):
        self.unlock_seconds = cfg.unlock_seconds
        self.status = '模擬模式（未接 Arduino）'
        self.connected = True
        self.lock = threading.Lock()
        self.timer = None

    def connect(self):
        log.info('[模擬 Arduino] 已就緒 → 門鎖：上鎖')
        return True

    def _auto_lock(self):
        log.info('[模擬 Arduino] %.0f 秒到 → 自動上鎖 [LOCKED]', self.unlock_seconds)

    def send(self, command):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if command == 'UNLOCK':
                log.info('[模擬 Arduino] 繼電器 ON → 開鎖 [UNLOCKED]（%.0f 秒後自動上鎖）', self.unlock_seconds)
                self.timer = threading.Timer(self.unlock_seconds, self._auto_lock)
                self.timer.daemon = True
                self.timer.start()
                return True, '[模擬] Arduino 開鎖'
            log.info('[模擬 Arduino] 繼電器 OFF → 上鎖 [LOCKED]')
            return True, '[模擬] Arduino 上鎖'

    def watchdog(self, stop):
        stop.wait()

    def close(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
