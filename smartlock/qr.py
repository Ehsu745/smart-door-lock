"""控制頁網址與 QR code（終端機版、圖片版）。沒有安裝 qrcode 套件時自動略過。"""
from __future__ import annotations

import shutil
import socket
import subprocess

import cv2
import numpy as np

from .config import Config


def lan_ip() -> str:
    """取得本機在區網的 IP（不會真的送出封包）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return subprocess.run(['hostname', '-I'], capture_output=True, text=True).stdout.split()[0]
        except (OSError, IndexError):
            return '127.0.0.1'


def control_url(cfg: Config) -> str:
    return cfg.public_url or f"{'https' if cfg.web_https else 'http'}://{lan_ip()}:{cfg.web_port}"


def qr_matrix(text):
    """回傳 QR code 的布林矩陣（含白邊）；沒有安裝 qrcode 套件時回傳 None。"""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def print_qr(text) -> None:
    """在終端機用 Unicode 半格字元印出 QR code（SSH 也能顯示）。"""
    m = qr_matrix(text)
    if m is None:
        if shutil.which('qrencode'):
            subprocess.run(['qrencode', '-t', 'ansiutf8', text])
        else:
            print('（沒有 qrcode 套件，無法印出 QR code：pip install qrcode）')
        print(text)
        return
    if len(m) % 2:
        m = m + [[False] * len(m[0])]
    # 以「亮底暗格」輸出：深色終端機背景下手機也能掃
    chars = {(False, False): '█', (True, True): ' ', (True, False): '▄', (False, True): '▀'}
    lines = [''.join(chars[(top, bottom)] for top, bottom in zip(m[r], m[r + 1])) for r in range(0, len(m), 2)]
    print('\n'.join(lines))
    print(text, flush=True)


def qr_image(text, size: int):
    """回傳 size×size 的 BGR QR code 圖片；沒有 qrcode 套件時回傳 None。"""
    m = qr_matrix(text)
    if m is None:
        return None
    img = np.where(np.array(m, dtype=bool), 0, 255).astype(np.uint8)
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
