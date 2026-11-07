"""預覽視窗與門口顯示畫面（OpenCV 只能畫英文，所以畫面文字用英文）。"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

from .config import Config
from .qr import qr_image

WINDOW_NAME = 'Smart Door Lock'
PANEL_W = 300
STATUS_BAR_H = 56


def has_display() -> bool:
    if sys.platform == 'darwin':          # macOS 沒有 DISPLAY 變數，但一定有桌面
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def want_window(cfg: Config) -> bool:
    if cfg.show_window == '1':
        return True
    if cfg.show_window == '0':
        return False
    return has_display()


def draw_bar(img, x, y, w, h, frac, color):
    cv2.rectangle(img, (x, y), (x + w, y + h), (60, 60, 60), -1)
    cv2.rectangle(img, (x, y), (x + int(w * max(0.0, min(1.0, frac))), y + h), color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (230, 230, 230), 1)


def draw_label(img, text, org, scale=0.7, color=(255, 255, 255), bg=(0, 0, 0)):
    """有底色的文字，在亮背景上也看得清楚。"""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x, y = org
    cv2.rectangle(img, (x - 4, y - th - 6), (x + tw + 4, y + base + 2), bg, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


def door_status(system, cfg: Config):
    """回傳 (顏色, 主文字, 副文字)，給狀態列／面板使用。"""
    with system.guard:
        st = dict(system.state)
    remain = system.unlocked_until - system.clock()
    if remain > 0:
        return (60, 160, 60), 'UNLOCKED', f'auto-lock in {remain:.0f}s'
    if st.get('visitor'):
        return (160, 40, 160), 'GUEST AT DOOR', f'{st["visitor"]} - unlock via web'
    if st.get('phase') == 'gesture':
        want = 'your gesture' if st.get('target_bound') else ' / '.join(cfg.allowed_gestures)
        return (0, 150, 230), 'SHOW GESTURE', f'{want}  {st.get("countdown") or 0:.0f}s'
    return (50, 50, 190), 'LOCKED', ('auto unlock: on' if st.get('auto') else 'auto unlock: OFF')


def draw_rec(frame, system):
    """哨兵錄影中：畫面右上角顯示紅點 REC（讓門口的人知道正在錄影）。"""
    provider = getattr(system, 'providers', {}).get('sentinel')
    try:
        st = provider() if provider else None
    except Exception:
        st = None
    if st and st.get('state') == 'recording':
        w = frame.shape[1]
        cv2.circle(frame, (w - 78, 26), 9, (0, 0, 230), -1)
        draw_label(frame, 'REC', (w - 62, 34), 0.7, (255, 255, 255), (0, 0, 180))


def compose_display(system, cfg: Config, url: str, qr):
    """門口顯示畫面。有 QR：右邊面板（QR + 狀態）；沒有 QR：畫面底部加狀態列。"""
    with system.guard:
        frame = None if system.frame is None else system.frame.copy()
    if frame is None:
        frame = np.zeros((cfg.frame_size[1], cfg.frame_size[0], 3), np.uint8)
        cv2.putText(frame, 'Starting camera...', (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
    draw_rec(frame, system)
    color, text, sub = door_status(system, cfg)
    font = cv2.FONT_HERSHEY_SIMPLEX
    if not cfg.enable_qr:
        fh, fw = frame.shape[:2]
        bar = np.zeros((STATUS_BAR_H, fw, 3), np.uint8)
        bar[:] = color
        cv2.putText(bar, text, (14, 38), font, 1.0, (255, 255, 255), 2)
        cv2.putText(bar, sub, (fw - 20 - cv2.getTextSize(sub, font, 0.6, 1)[0][0], 36), font, 0.6, (255, 255, 255), 1)
        return np.vstack([frame, bar])
    h = frame.shape[0]
    panel = np.full((h, PANEL_W, 3), 255, np.uint8)
    y = 16
    if qr is not None:
        q = min(PANEL_W - 30, h - 190)
        x0 = (PANEL_W - q) // 2
        panel[y:y + q, x0:x0 + q] = cv2.resize(qr, (q, q), interpolation=cv2.INTER_NEAREST)
        y += q + 26
        cv2.putText(panel, 'Scan for control page', (14, y), font, 0.62, (60, 60, 60), 2)
        y += 22
    short = url.replace('http://', '')
    cv2.putText(panel, short, (14, y), font, 0.5 if len(short) < 26 else 0.42, (90, 90, 90), 1)
    y += 18
    pins = getattr(system, 'pins', None)
    if (pins.required if pins is not None else cfg.control_pin):
        cv2.putText(panel, '(PIN required)', (14, y), font, 0.45, (120, 120, 120), 1)
    cv2.rectangle(panel, (10, h - 82), (PANEL_W - 10, h - 12), color, -1)
    cv2.putText(panel, text, (22, h - 46), font, 0.95, (255, 255, 255), 2)
    cv2.putText(panel, sub, (22, h - 22), font, 0.5, (255, 255, 255), 1)
    return np.hstack([frame, panel])


def display_loop(system, cfg: Config, url: str) -> None:
    """在主執行緒顯示門口視窗（macOS 規定 GUI 要在主執行緒）。按 q 或 Esc 結束整個系統。"""
    qr = qr_image(url, 512) if cfg.enable_qr else None
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)   # 不顯示 OpenCV 工具列
    if cfg.kiosk:
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        cv2.resizeWindow(WINDOW_NAME, cfg.frame_size[0] + (PANEL_W if cfg.enable_qr else 0),
                         cfg.frame_size[1] + (0 if cfg.enable_qr else STATUS_BAR_H))
    while not system.stop.is_set():
        cv2.imshow(WINDOW_NAME, compose_display(system, cfg, url, qr))
        if cv2.waitKey(40) & 0xFF in (ord('q'), 27):
            break
    cv2.destroyAllWindows()
