"""Flask 控制頁。

  /                         控制頁（即時影像、開鎖、狀態）
  /records                  哨兵紀錄頁（錄影、事件、容量）
  /api/status               狀態（每秒輪詢）
  /api/action               開鎖 / 上鎖 / 恢復自動（需要 PIN）
  /api/login                輸入 PIN 換取登入狀態（看紀錄用）
  /api/records、/api/events 紀錄清單（需要登入）
  /records/<id>/play|snapshot.jpg|download、DELETE /api/records/<id>

有設定 CONTROL_PIN 時：開鎖要 PIN；紀錄裡有人臉影像，查看也要 PIN。
PIN 連續錯 5 次會鎖 60 秒，避免被暴力猜測。
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent

MAX_FAILS = 5
LOCKOUT_SECONDS = 60.0


class PinGuard:
    """PIN 比對 + 連續錯誤鎖定（以來源 IP 計算）。"""

    def __init__(self, pin: str, clock=time.monotonic):
        self.pin = pin
        self.clock = clock
        self._fails = {}                 # ip → (次數, 第一次錯誤時間)
        self._locked = {}                # ip → 解鎖時間
        self._lock = threading.Lock()

    @property
    def required(self) -> bool:
        return bool(self.pin)

    def locked_for(self, ip) -> float:
        with self._lock:
            return max(0.0, self._locked.get(ip, 0.0) - self.clock())

    def check(self, ip, pin) -> bool:
        if not self.pin:
            return True
        if self.locked_for(ip) > 0:
            return False
        ok = hmac.compare_digest(str(pin or ''), self.pin)
        with self._lock:
            if ok:
                self._fails.pop(ip, None)
            else:
                count, first = self._fails.get(ip, (0, self.clock()))
                if self.clock() - first > LOCKOUT_SECONDS:
                    count, first = 0, self.clock()
                count += 1
                self._fails[ip] = (count, first)
                if count >= MAX_FAILS:
                    self._locked[ip] = self.clock() + LOCKOUT_SECONDS
                    self._fails.pop(ip, None)
        return ok


def create_web(system, storage=None):
    from flask import (Flask, Response, abort, jsonify, request, send_file, send_from_directory, session)

    cfg = system.cfg
    app = Flask(__name__, static_folder=str(WEB_DIR / 'static'), static_url_path='/static')
    app.secret_key = os.urandom(32)                               # 重新啟動後需要重新輸入 PIN
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    logging.getLogger('werkzeug').setLevel(logging.WARNING)      # 不印每秒一次的 /api/status 請求紀錄
    guard = PinGuard(cfg.control_pin)
    app.pin_guard = guard

    def pin_ok(pin) -> bool:
        ip = request.remote_addr
        if guard.locked_for(ip) > 0:
            return False
        if guard.check(ip, pin):
            return True
        system.emit('pin_rejected', f'網頁 PIN 錯誤（{ip}）', remote=ip)
        return False

    def deny():
        wait = guard.locked_for(request.remote_addr)
        msg = f'PIN 錯誤次數過多，請 {wait:.0f} 秒後再試' if wait > 0 else 'PIN 錯誤'
        return jsonify(success=False, message=msg), (429 if wait > 0 else 403)

    def logged_in() -> bool:
        return not guard.required or session.get('auth') is True

    def need_login():
        if not logged_in():
            abort(401)
        if storage is None:
            abort(404)

    # ==========================================
    # 控制頁
    # ==========================================
    @app.route('/')
    def index():
        return send_from_directory(WEB_DIR / 'templates', 'index.html')

    @app.route('/api/status')
    def status():
        return jsonify(system.snapshot())

    @app.route('/api/action', methods=['POST'])
    def action():
        data = request.get_json(silent=True) or {}
        act = data.get('action')
        if act not in ('auto', 'lock', 'unlock'):
            return jsonify(success=False, message='無效指令'), 400
        if guard.required and not pin_ok(data.get('pin')):
            return deny()
        if act == 'auto':
            system.update(auto=True, message='已恢復自動解鎖')
            system.emit('auto_resumed', '手動：恢復自動解鎖')
            return jsonify(success=True, message='已恢復自動解鎖')
        if act == 'lock':
            system.update(auto=False)
            ok, msg = system.door('LOCK', '手動上鎖')
        else:
            ok, msg = system.door('UNLOCK', '手動解鎖')
        return jsonify(success=ok, message=msg), (200 if ok else 503)

    @app.route('/video_feed')
    def video_feed():
        def frames():
            while not system.stop.is_set():
                with system.guard:
                    frame = system.jpeg
                if frame is not None:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                time.sleep(0.08)
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    # ==========================================
    # 登入（看紀錄用）
    # ==========================================
    @app.route('/api/login', methods=['POST'])
    def login():
        if not guard.required:
            return jsonify(success=True, required=False)
        data = request.get_json(silent=True) or {}
        if not pin_ok(data.get('pin')):
            return deny()
        session['auth'] = True
        return jsonify(success=True, required=True)

    @app.route('/api/session')
    def session_info():
        return jsonify(required=guard.required, logged_in=logged_in(), sentinel=storage is not None)

    # ==========================================
    # 哨兵紀錄
    # ==========================================
    @app.route('/records')
    def records_page():
        return send_from_directory(WEB_DIR / 'templates', 'records.html')

    @app.route('/api/records')
    def records():
        need_login()
        limit = min(int(request.args.get('limit', 30)), 200)
        before = request.args.get('before', type=int)
        return jsonify(records=storage.list_records(limit, before), usage=storage.usage().as_dict())

    @app.route('/api/events')
    def events():
        need_login()
        limit = min(int(request.args.get('limit', 100)), 500)
        kinds = [k for k in request.args.get('kinds', '').split(',') if k] or None
        return jsonify(events=storage.list_events(limit, kinds))

    def record_file(rid, field):
        need_login()
        rec = storage.get(rid)
        if not rec or not rec[field]:
            abort(404)
        path = storage.path(rec[field])
        if not path.exists():
            abort(404)
        return rec, path

    @app.route('/records/<int:rid>/snapshot.jpg')
    def snapshot(rid):
        _, path = record_file(rid, 'snapshot')
        return send_file(path, mimetype='image/jpeg', max_age=3600)

    @app.route('/records/<int:rid>/download')
    def download(rid):
        _, path = record_file(rid, 'video')
        return send_file(path, mimetype='video/x-msvideo', as_attachment=True, download_name=path.name)

    @app.route('/records/<int:rid>/play')
    def play(rid):
        """把 AVI(MJPG) 逐幀轉成 MJPEG 串流：任何瀏覽器的 <img> 都能播，不需要 H.264 編碼器。"""
        import cv2
        _, path = record_file(rid, 'video')
        speed = max(0.5, min(4.0, request.args.get('speed', 1.0, type=float)))

        def frames():
            cap = cv2.VideoCapture(str(path))
            delay = 1.0 / ((cap.get(cv2.CAP_PROP_FPS) or cfg.sentinel_fps) * speed)
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n'
                    time.sleep(delay)
            finally:
                cap.release()
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/api/records/<int:rid>', methods=['DELETE'])
    def delete_record(rid):
        need_login()
        if not storage.delete(rid):
            return jsonify(success=False, message='找不到這段錄影，或還在錄影中'), 404
        system.emit('info', f'網頁刪除錄影 #{rid}')
        return jsonify(success=True)

    @app.errorhandler(401)
    def unauthorized(_):
        return jsonify(success=False, message='需要輸入 PIN'), 401

    return app
