"""Flask 控制頁（單頁 App：門鎖 / 成員 / 紀錄 / 設定）。

  /                               單頁 App
  /api/status                     狀態（每秒輪詢，不需登入）
  /api/action                     unlock / lock / auto_on / auto_off（需登入或附 PIN）
  /video_feed                     即時影像 MJPEG
  /api/session、/api/login、/api/logout、/api/pin（變更 PIN）
  /api/users、/api/users/<name>/photo
  /api/events、/api/records、/records/<id>/play|snapshot.jpg|download、DELETE /api/records/<id>
  /api/settings

有 PIN 時：開鎖、看紀錄、看成員、看設定都要先登入（輸入一次 PIN，這支手機在本次啟動期間都有效）。
PIN 連續錯 5 次鎖 60 秒；改 PIN 後所有手機都要重新輸入。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .. import __version__
from ..security import PIN_RULE, PinStore

WEB_DIR = Path(__file__).resolve().parent

MAX_FAILS = 5
LOCKOUT_SECONDS = 60.0


class PinGuard:
    """PIN 比對 + 連續錯誤鎖定（以來源 IP 計算）。store 可以是 PinStore 或單純的 PIN 字串。"""

    def __init__(self, store, clock=time.monotonic):
        self.store = store if hasattr(store, 'verify') else _StaticPin(store)
        self.clock = clock
        self._fails = {}                 # ip → (次數, 第一次錯誤時間)
        self._locked = {}                # ip → 解鎖時間
        self._lock = threading.Lock()

    @property
    def required(self) -> bool:
        return self.store.required

    def locked_for(self, ip) -> float:
        with self._lock:
            return max(0.0, self._locked.get(ip, 0.0) - self.clock())

    def check(self, ip, pin) -> bool:
        if not self.store.required:
            return True
        if self.locked_for(ip) > 0:
            return False
        ok = self.store.verify(pin)
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


class _StaticPin:
    def __init__(self, pin):
        self.pin, self.version = pin, 0

    @property
    def required(self):
        return bool(self.pin)

    def verify(self, pin):
        import hmac
        return hmac.compare_digest(str(pin or ''), self.pin)


def create_web(system, storage=None, line=None):
    from flask import (Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory, session)

    cfg = system.cfg
    if system.pins is None:
        system.pins = PinStore(cfg.pin_file, cfg.control_pin)
    store = system.pins
    app = Flask(__name__, static_folder=str(WEB_DIR / 'static'), static_url_path='/static')
    app.secret_key = os.urandom(32)                               # 重新啟動後需要重新輸入 PIN
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    logging.getLogger('werkzeug').setLevel(logging.WARNING)      # 不印每秒一次的 /api/status 請求紀錄
    guard = PinGuard(store)
    app.pin_guard = guard

    # ==========================================
    # 登入與權限
    # ==========================================
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
        return jsonify(success=False, message=msg, locked=wait > 0), (429 if wait > 0 else 403)

    def logged_in() -> bool:
        return not store.required or session.get('auth') == store.version

    def login_session():
        session['auth'] = store.version
        session.permanent = False

    def need_login():
        if not logged_in():
            abort(401)

    def need_storage():
        need_login()
        if storage is None:
            abort(404)

    @app.errorhandler(401)
    def unauthorized(_):
        return jsonify(success=False, message='需要輸入 PIN', login=True), 401

    @app.route('/api/session')
    def session_info():
        return jsonify(required=store.required, logged_in=logged_in(), sentinel=storage is not None)

    @app.route('/api/login', methods=['POST'])
    def login():
        if not store.required:
            return jsonify(success=True, required=False)
        data = request.get_json(silent=True) or {}
        if not pin_ok(data.get('pin')):
            return deny()
        login_session()
        return jsonify(success=True, required=True)

    @app.route('/api/logout', methods=['POST'])
    def logout():
        session.pop('auth', None)
        return jsonify(success=True)

    @app.route('/api/pin', methods=['POST'])
    def change_pin():
        """變更 PIN：要先通過目前的 PIN（沒有 PIN 時可直接設定）。"""
        data = request.get_json(silent=True) or {}
        if store.required and not pin_ok(data.get('old')):
            return deny()
        new = str(data.get('new') or '')
        if not PIN_RULE.fullmatch(new):
            return jsonify(success=False, message='新 PIN 要是 4 到 8 位數字'), 400
        if not hasattr(store, 'set'):
            return jsonify(success=False, message='這個模式不能從網頁改 PIN'), 400
        store.set(new)
        login_session()                                            # 自己這支手機保持登入，其他手機要重新輸入
        system.emit('pin_changed', f'網頁變更 PIN（{request.remote_addr}）', remote=request.remote_addr)
        return jsonify(success=True, message='PIN 已變更，其他手機需要重新輸入')

    # ==========================================
    # 門鎖
    # ==========================================
    @app.route('/')
    def index():
        return send_from_directory(WEB_DIR / 'templates', 'index.html')

    @app.route('/records')
    def records_page():
        return redirect('/#history')

    @app.route('/api/status')
    def status():
        return jsonify(system.snapshot())

    @app.route('/api/action', methods=['POST'])
    def action():
        data = request.get_json(silent=True) or {}
        act = data.get('action')
        if act == 'auto':
            act = 'auto_on'
        if act not in ('auto_on', 'auto_off', 'lock', 'unlock'):
            return jsonify(success=False, message='無效指令'), 400
        if not logged_in():
            if not data.get('pin'):
                abort(401)                                         # 還沒登入、也沒附 PIN：請前端跳出鍵盤，不算錯誤
            if not pin_ok(data.get('pin')):
                return deny()
            login_session()
        if act == 'auto_on':
            system.update(auto=True, message='自動解鎖已開啟')
            system.emit('auto_resumed', '自動解鎖已開啟（手機）')
            return jsonify(success=True, message='自動解鎖已開啟')
        if act == 'auto_off':
            system.update(auto=False, message='自動解鎖已關閉')
            system.emit('auto_paused', '自動解鎖已關閉（手機）')
            return jsonify(success=True, message='自動解鎖已關閉，只能用手機開門')
        if act == 'lock':
            ok, msg = system.door('LOCK', '手動上鎖（手機）')
            return jsonify(success=ok, message='已上鎖' if ok else msg), (200 if ok else 503)
        ok, msg = system.door('UNLOCK', '手動解鎖（手機）')
        return jsonify(success=ok, message='已開鎖' if ok else msg), (200 if ok else 503)

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
    # 成員
    # ==========================================
    def members():
        mapping = getattr(system.recognizer, 'mapping', {}) or {}
        return {name: kind for name, kind in mapping.values()}

    def photos_of(name):
        return sorted(cfg.face_dir.glob(f'{name}_*.jpg'))

    @app.route('/api/users')
    def users():
        need_login()
        seen = storage.last_seen() if storage is not None else {}
        out = [{'name': n, 'role': k, 'photos': len(photos_of(n)), 'last_seen': seen.get(n)}
               for n, k in sorted(members().items())]
        return jsonify(users=out)

    @app.route('/api/users/<name>/photo')
    def user_photo(name):
        need_login()
        if name not in members():
            abort(404)
        files = photos_of(name)
        if not files:
            abort(404)
        return send_file(files[len(files) // 2], mimetype='image/jpeg', max_age=600)

    # ==========================================
    # 紀錄
    # ==========================================
    @app.route('/api/events')
    def events():
        need_storage()
        limit = min(int(request.args.get('limit', 100)), 500)
        kinds = [k for k in request.args.get('kinds', '').split(',') if k] or None
        items = storage.list_events(limit, kinds, request.args.get('before', type=int))
        for e in items:
            rid = e['data'].get('record_id') if e['kind'] == 'sentinel_saved' else None
            if rid:
                rec = storage.get(rid)
                e['clip'] = ({'id': rid, 'duration': rec['duration'], 'snapshot': bool(rec['snapshot']),
                              'status': rec['status'], 'size_bytes': rec['size_bytes']} if rec
                             else {'id': rid, 'deleted': True})
        return jsonify(events=items)

    @app.route('/api/records')
    def records():
        need_storage()
        limit = min(int(request.args.get('limit', 30)), 200)
        before = request.args.get('before', type=int)
        return jsonify(records=storage.list_records(limit, before), usage=storage.usage().as_dict())

    def record_file(rid, field):
        need_storage()
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
        need_storage()
        if not storage.delete(rid):
            return jsonify(success=False, message='找不到這段錄影，或還在錄影中'), 404
        system.emit('info', f'網頁刪除錄影 #{rid}')
        return jsonify(success=True, message=f'已刪除錄影 #{rid}')

    # ==========================================
    # 設定
    # ==========================================
    @app.route('/api/settings')
    def settings():
        need_login()
        line_info = line.quota(refresh=request.args.get('refresh') == '1') if line else {'configured': bool(cfg.line_token)}
        line_info = dict(line_info, basic_id=cfg.line_basic_id, target='user' if cfg.line_user_id else 'broadcast')
        snap = system.snapshot()
        return jsonify(
            line=line_info,
            storage=storage.usage().as_dict() if storage is not None else None,
            sentinel={'enabled': cfg.sentinel and storage is not None, 'trigger': cfg.sentinel_trigger,
                      'stop': cfg.sentinel_stop, 'max': cfg.sentinel_max, 'cooldown': cfg.sentinel_cooldown},
            system={'arduino': snap['arduino'], 'connected': snap['connected'],
                    'camera': system.info.get('camera', ''), 'gesture': system.info.get('gesture', ''),
                    'rule': ('人臉＋手勢（' + ' / '.join(cfg.gesture_labels()) + '）') if cfg.require_gesture else '只需人臉',
                    'unlock_seconds': cfg.unlock_seconds, 'version': __version__},
            security={'pin_required': store.required, 'source': getattr(store, 'source', 'env')},
        )

    return app
