"""相機：Pi 相機（rpicam-vid 或 Picamera2），或 USB / Mac 內建 webcam。"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

from .config import Config

log = logging.getLogger(__name__)


class RpicamCamera:
    """用 rpicam-vid 把 Pi 相機的 YUV420 原始畫面輸出到 stdout，背景執行緒持續讀最新一幀。
    不需要 Picamera2 / libcamera 的 Python 套件，所以任何 Python 版本（含 MediaPipe 用的 3.11）都能用。"""

    def __init__(self, size=(640, 480)):
        self.w, self.h = size
        self.frame_bytes = self.w * self.h * 3 // 2
        cmd = ['rpicam-vid', '-t', '0', '-n', '--codec', 'yuv420', '--width', str(self.w),
               '--height', str(self.h), '--framerate', '30', '--flush', '-o', '-']
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.latest = None
        self.seq = 0
        self._last_seq = 0
        self.cond = threading.Condition()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        deadline = time.monotonic() + 8
        with self.cond:
            while self.latest is None and time.monotonic() < deadline and self.proc.poll() is None:
                self.cond.wait(0.2)
        if self.latest is None:
            self.close()
            raise RuntimeError('rpicam-vid 沒有輸出畫面：確認 rpicam-hello --list-cameras 看得到相機，'
                               '且沒有其他程式正在使用相機')

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _reader(self):
        while self.alive:
            raw = self._read_exact(self.frame_bytes)
            if raw is None:
                break
            yuv = np.frombuffer(raw, np.uint8).reshape(self.h * 3 // 2, self.w)
            frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            with self.cond:
                self.latest = frame
                self.seq += 1
                self.cond.notify_all()
        self.alive = False
        with self.cond:
            self.cond.notify_all()

    def read(self):
        """回傳比上一次更新的畫面（等最多 1 秒），讀不到回傳 None。"""
        with self.cond:
            last = self._last_seq
            self.cond.wait_for(lambda: self.seq != last or not self.alive, timeout=1.0)
            if self.seq == last:
                return None
            self._last_seq = self.seq
            return self.latest.copy()

    def close(self):
        self.alive = False
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Camera:
    """依設定與環境挑選相機：auto 時依序嘗試 rpicam-vid → Picamera2 → USB / 內建 webcam。"""

    def __init__(self, cfg: Config):
        self.impl = None
        self.picam = None
        self.cap = None
        want = cfg.camera
        size = cfg.frame_size
        if want in ('auto', 'rpicam') and sys.platform.startswith('linux') and shutil.which('rpicam-vid'):
            try:
                self.impl = RpicamCamera(size)
                log.info('相機：Pi 相機（rpicam-vid）')
                return
            except RuntimeError as exc:
                if want == 'rpicam':
                    raise
                log.info('rpicam-vid 失敗（%s），改試其他方式', exc)
        if want in ('auto', 'picamera2'):
            try:
                from picamera2 import Picamera2
                if Picamera2.global_camera_info():
                    self.picam = Picamera2()
                    self.picam.configure(self.picam.create_video_configuration(
                        main={'size': size, 'format': 'RGB888'}))      # RGB888 的記憶體順序即 BGR
                    self.picam.start()
                    time.sleep(1)
                    log.info('相機：Pi 相機（Picamera2）')
                    return
            except ImportError:
                if want == 'picamera2':
                    raise RuntimeError('這個 Python 環境沒有 picamera2')
        self.cap = cv2.VideoCapture(cfg.camera_index)
        if not self.cap.isOpened():
            if sys.platform == 'darwin':
                raise RuntimeError(f'鏡頭 {cfg.camera_index} 無法開啟：到「系統設定 → 隱私權與安全性 → 相機」'
                                   '允許終端機 App，重開終端機；或試 CAMERA_INDEX=1')
            raise RuntimeError('鏡頭無法開啟：先用 rpicam-hello --list-cameras 確認相機，'
                               '並關閉其他使用相機的程式')
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        log.info('相機：USB / 內建 webcam（編號 %s）', cfg.camera_index)

    def read(self):
        if self.impl is not None:
            return self.impl.read()
        if self.picam is not None:
            return self.picam.capture_array()
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        if self.impl is not None:
            self.impl.close()
        if self.picam is not None:
            self.picam.stop()
            self.picam.close()
        if self.cap is not None:
            self.cap.release()


class FrameReader:
    """包住任何有 read() 的相機：連續讀不到畫面太多次就丟出錯誤，避免無聲卡死。"""

    def __init__(self, camera, max_failures: int = 50):
        self.camera = camera
        self.max_failures = max_failures
        self.failures = 0

    def read(self):
        frame = self.camera.read()
        if frame is None:
            self.failures += 1
            if self.failures >= self.max_failures:
                raise RuntimeError(f'鏡頭連續 {self.max_failures} 次讀不到畫面')
            time.sleep(0.05)
        else:
            self.failures = 0
        return frame
