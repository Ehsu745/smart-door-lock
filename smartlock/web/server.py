"""Flask 控制頁：/ 頁面、/api/status 狀態、/api/action 開鎖上鎖、/video_feed 即時影像。"""
from __future__ import annotations

import hmac
import logging
import time
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent


def create_web(system):
    from flask import Flask, Response, jsonify, request, send_from_directory

    cfg = system.cfg
    app = Flask(__name__, static_folder=str(WEB_DIR / 'static'), static_url_path='/static')
    logging.getLogger('werkzeug').setLevel(logging.WARNING)      # 不印每秒一次的 /api/status 請求紀錄

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
        if cfg.control_pin and not hmac.compare_digest(str(data.get('pin', '')), cfg.control_pin):
            system.emit('pin_rejected', f'網頁 {act} 被拒：PIN 錯誤（{request.remote_addr}）', remote=request.remote_addr)
            return jsonify(success=False, message='PIN 錯誤'), 403
        if act == 'auto':
            system.update(auto=True, message='已恢復自動解鎖')
            system.emit('auto_resumed', '手動：恢復自動解鎖')
            return jsonify(success=True, message='已恢復自動解鎖')
        if act == 'lock':
            system.update(auto=False)
            ok, msg = system.door('LOCK', '手動上鎖')
        elif act == 'unlock':
            ok, msg = system.door('UNLOCK', '手動解鎖')
        else:
            return jsonify(success=False, message='無效指令'), 400
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

    return app
