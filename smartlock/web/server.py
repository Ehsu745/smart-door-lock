"""Flask 控制頁（單頁 App：門鎖 / 成員 / 紀錄 / 設定）。

  /                               單頁 App
  /api/status                     狀態（每秒輪詢，不需登入）
  /api/action                     unlock / lock / auto_on / auto_off（需登入或附 PIN）
  /video_feed                     即時影像 MJPEG
  /api/session、/api/login、/api/logout、/api/pin（變更 PIN）
  /api/users、/api/users/<name>/photo、/api/users/<name>/role|gesture|delete（後三者每次都要 PIN）、/api/gestures
  /api/attempts/<id>/crop.jpg、/api/attempts/<id>/report|undo、/api/users/<name>/threshold/reset（後三者每次都要 PIN）
  /api/events、/api/records、/records/<id>/play|snapshot.jpg|download、DELETE /api/records/<id>
  /api/settings
  /api/intercom、/audio_feed（門口聲音，raw PCM）、/api/intercom/play（預錄語音；需登入或附 PIN）
  /api/intercom/talk（開始按住說話；需登入或附 PIN）→ /api/intercom/talk/<id>（POST raw PCM）→ /api/intercom/talk/<id>/end

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
from ..bindings import GESTURES, GestureBindings
from ..config import GESTURE_LABEL
from ..face import ROLES, delete_member, photos_of as face_photos, set_role
from ..security import PIN_RULE, PinStore

WEB_DIR = Path(__file__).resolve().parent

# 時間軸上可以回報誤認的事件（一次嘗試只在最後結果那筆顯示，face_passed 不重複顯示）
# gesture_wrong：人臉過了但手勢錯 = 可能是冒充者，主人最該看的一筆
ATTEMPT_EVENTS = ('unlock', 'gesture_timeout', 'gesture_cancel', 'door_failed', 'gesture_wrong')

MAX_FAILS = 5
LOCKOUT_SECONDS = 60.0

# 即時影像串流的限制：瀏覽器對同一個網站最多只開約 6 條連線，每條 MJPEG 串流會一直佔著一條。
# 舊分頁（例如每次掃 QR 都開新分頁）沒關的話，第 7 頁就打不開，看起來像「網站當掉」。
MAX_STREAMS = 4               # 全部手機合計；超過就關掉最舊的
STREAM_MAX_SECONDS = 600      # 單條串流最長 10 分鐘，前端會自動重連
STREAM_SEND_TIMEOUT = 20      # 手機休眠、離開 Wi-Fi 卡住超過 20 秒就斷線，釋放執行緒


class StreamRegistry:
    """追蹤正在播放的串流：同一台裝置開新串流時，關掉它之前的串流。"""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._next = 0
        self._live = {}          # id → (ip, 開始時間)
        self._kicked = set()
        self._lock = threading.Lock()

    def open(self, ip) -> int:
        with self._lock:
            self._next += 1
            sid = self._next
            for old, (old_ip, _) in self._live.items():
                if old_ip == ip:
                    self._kicked.add(old)
            self._live[sid] = (ip, self.clock())
            alive = [k for k in self._live if k not in self._kicked]
            for old in sorted(alive)[:-MAX_STREAMS] if len(alive) > MAX_STREAMS else []:
                self._kicked.add(old)
            return sid

    def active(self, sid) -> bool:
        with self._lock:
            if sid in self._kicked or sid not in self._live:
                return False
            return self.clock() - self._live[sid][1] < STREAM_MAX_SECONDS

    def close(self, sid):
        with self._lock:
            self._live.pop(sid, None)
            self._kicked.discard(sid)

    def count(self) -> int:
        with self._lock:
            return len([k for k in self._live if k not in self._kicked])


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


def create_web(system, storage=None, line=None, notifier=None, hardening=None, intercom=None):
    from flask import (Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory, session)

    cfg = system.cfg
    if system.pins is None:
        system.pins = PinStore(cfg.pin_file, cfg.control_pin)
    store = system.pins
    if system.bindings is None:
        system.bindings = GestureBindings(cfg.gestures_file)
    bindings = system.bindings
    app = Flask(__name__, static_folder=str(WEB_DIR / 'static'), static_url_path='/static')
    app.secret_key = os.urandom(32)                               # 重新啟動後需要重新輸入 PIN
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      MAX_CONTENT_LENGTH=1 << 20)                  # 最大的上傳是按住說話的聲音（每段 ≤ 160 KB）
    logging.getLogger('werkzeug').setLevel(logging.WARNING)      # 不印每秒一次的 /api/status 請求紀錄
    guard = PinGuard(store)
    app.pin_guard = guard
    streams = StreamRegistry()
    app.streams = streams
    audio_streams = StreamRegistry()                              # 聲音和影像各自計算，規則相同
    app.audio_streams = audio_streams
    if intercom is not None:
        system.providers['intercom'] = lambda: {k: v for k, v in intercom.status().items() if k != 'listeners'}
    started = time.time()

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
        sid = streams.open(request.remote_addr)

        def frames():
            try:
                while not system.stop.is_set() and streams.active(sid):
                    with system.guard:
                        frame = system.jpeg
                    if frame is not None:
                        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                    time.sleep(0.08)
            finally:
                streams.close(sid)
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame',
                        headers={'Cache-Control': 'no-store'})

    # ==========================================
    # 對講機
    # ==========================================
    def need_intercom():
        need_login()
        if intercom is None:
            abort(404)

    @app.route('/api/intercom')
    def intercom_info():
        need_intercom()
        return jsonify(success=True, clips=intercom.clips(), **intercom.status())

    @app.route('/audio_feed')
    def audio_feed():
        """門口聲音：單聲道 16-bit little-endian PCM，取樣率在 X-Sample-Rate。前端用 Web Audio 播。
        跟影像（/video_feed）一樣不需要 PIN；對門口播放（/api/intercom/play）才要。"""
        if intercom is None or not intercom.can_listen:
            abort(404)
        from ..intercom import RATE
        sid = audio_streams.open(request.remote_addr)
        q = intercom.subscribe(request.remote_addr)

        def chunks():
            from queue import Empty
            try:
                while not system.stop.is_set() and audio_streams.active(sid):
                    try:
                        data = q.get(timeout=1)
                    except Empty:
                        continue
                    if data is None:
                        break
                    yield data
            finally:
                intercom.unsubscribe(q)
                audio_streams.close(sid)
        return Response(chunks(), mimetype='application/octet-stream',
                        headers={'Cache-Control': 'no-store', 'X-Sample-Rate': str(RATE), 'X-Accel-Buffering': 'no'})

    def login_or_pin(data):
        """已登入，或這次附了正確 PIN（順便登入）。回傳錯誤回應；通過回傳 None。"""
        if not logged_in():
            if not data.get('pin'):
                abort(401)
            if not pin_ok(data.get('pin')):
                return deny()
            login_session()
        return None

    @app.route('/api/intercom/play', methods=['POST'])
    def intercom_play():
        data = request.get_json(silent=True) or {}
        refused = login_or_pin(data)
        if refused:
            return refused
        if intercom is None:
            abort(404)
        ok, msg = intercom.play(str(data.get('clip') or ''), request.remote_addr)
        return jsonify(success=ok, message=msg), (200 if ok else 409)

    @app.route('/api/intercom/talk', methods=['POST'])
    def intercom_talk_start():
        """按住說話：開始。之後手機每 0.1～0.3 秒 POST 一段 16 kHz 單聲道 S16LE 到 /api/intercom/talk/<id>。"""
        from ..intercom import RATE
        refused = login_or_pin(request.get_json(silent=True) or {})
        if refused:
            return refused
        if intercom is None or not intercom.can_talk:
            abort(404)
        ok, msg, tid = intercom.talk_start(request.remote_addr)
        if not ok:
            return jsonify(success=False, message=msg), 409
        return jsonify(success=True, id=tid, rate=RATE, max_seconds=intercom.talk_max)

    @app.route('/api/intercom/talk/<tid>', methods=['POST'])
    def intercom_talk_feed(tid):
        from ..intercom import TALK_MAX_BYTES
        need_intercom()
        if (request.content_length or 0) > TALK_MAX_BYTES:
            return jsonify(success=False, message='一次送太多聲音'), 413
        ok, msg = intercom.talk_feed(tid, request.get_data(cache=False))
        return jsonify(success=ok, message=msg), (200 if ok else 410)

    @app.route('/api/intercom/talk/<tid>/end', methods=['POST'])
    def intercom_talk_end(tid):
        need_intercom()
        intercom.talk_end(tid)
        return jsonify(success=True)

    @app.route('/api/health')
    def health():
        """排查用：串流數、執行緒數、開啟的檔案數。網站卡住時先看這裡。"""
        fds = None
        try:
            fds = len(os.listdir('/proc/self/fd'))
        except OSError:
            pass
        return jsonify(ok=True, uptime=round(time.time() - started), streams=streams.count(),
                       audio_streams=audio_streams.count(),
                       threads=threading.active_count(), open_files=fds)

    # ==========================================
    # 成員
    # ==========================================
    def members():
        mapping = getattr(system.recognizer, 'mapping', {}) or {}
        return {name: kind for name, kind in mapping.values()}

    def photos_of(name):
        return face_photos(cfg, name)

    def pin_every_time(data):
        """變更權限、刪除成員：每次都要輸入 PIN，已登入也不算。回傳錯誤回應，通過則回傳 None。"""
        if not store.required:
            return jsonify(success=False, message='還沒設定 PIN：先到「設定 → 安全」設定 PIN，才能變更成員'), 400
        if not data.get('pin'):
            return jsonify(success=False, message='需要輸入 PIN'), 403
        if not pin_ok(data.get('pin')):
            return deny()
        return None

    @app.route('/api/users')
    def users():
        need_login()
        seen = storage.last_seen() if storage is not None else {}
        reports = storage.report_counts() if storage is not None else {}
        thr = getattr(system.recognizer, 'threshold_for', None)
        bound = bindings.all()                   # 只有登入後的成員頁看得到；門口與 /api/status 不給
        out = [{'name': n, 'role': k, 'photos': len(photos_of(n)), 'last_seen': seen.get(n),
                'threshold': thr(n) if thr else cfg.threshold, 'default_threshold': cfg.threshold,
                'reports': reports.get(n, 0), 'gesture': bound.get(n)}
               for n, k in sorted(members().items())]
        return jsonify(users=out)

    @app.route('/api/users/<name>/role', methods=['POST'])
    def user_role(name):
        """變更主人 / 訪客。每次都要輸入 PIN：已登入（例如剛在門鎖頁開過門）也不算數。"""
        need_login()
        data = request.get_json(silent=True) or {}
        refused = pin_every_time(data)
        if refused:
            return refused
        role = data.get('role')
        if role not in ROLES:
            return jsonify(success=False, message='身分只能是主人或訪客'), 400
        if name not in members():
            abort(404)
        old = set_role(cfg, system.recognizer, name, role)
        label = {'master': '主人', 'guest': '訪客'}
        if old != role:
            system.emit('role_changed', f'網頁將 {name} 從{label.get(old, old)}改成{label[role]}（{request.remote_addr}）',
                        name=name, role=role, old=old, remote=request.remote_addr)
        masters = sum(1 for k in members().values() if k == 'master')
        return jsonify(success=True, message=f'{name} 已改成{label[role]}', masters=masters)

    @app.route('/api/users/<name>/gesture', methods=['POST'])
    def user_gesture(name):
        """綁定開鎖手勢；gesture 為 null 代表取消綁定（回到預設的讚 / YA）。每次都要 PIN。"""
        need_login()
        data = request.get_json(silent=True) or {}
        refused = pin_every_time(data)
        if refused:
            return refused
        gesture = data.get('gesture') or None
        if gesture is not None and gesture not in GESTURES:
            return jsonify(success=False, message='不支援這個手勢'), 400
        if name not in members():
            abort(404)
        changed = bindings.get(name) != gesture
        bindings.set(name, gesture)
        if changed:   # 事件紀錄不寫出手勢本身：看得到紀錄的人不一定是本人
            system.emit('gesture_changed', f'網頁{"設定" if gesture else "取消"} {name} 的開鎖手勢（{request.remote_addr}）',
                        name=name, remote=request.remote_addr)
        return jsonify(success=True, message=f'{name} 的開鎖手勢已{"更新" if gesture else "改回預設"}')

    @app.route('/api/gestures')
    def gesture_options():
        need_login()
        return jsonify(gestures=[{'id': g, 'label': GESTURE_LABEL[g]} for g in GESTURES],
                       default=list(cfg.allowed_gestures))

    @app.route('/api/users/<name>/delete', methods=['POST'])
    def user_delete(name):
        """刪除成員：照片、名單、模型特徵全部移除並重新訓練，無法復原。每次都要 PIN。"""
        need_login()
        data = request.get_json(silent=True) or {}
        refused = pin_every_time(data)
        if refused:
            return refused
        if name not in members():
            abort(404)
        if data.get('confirm') != name:          # 前端警告畫面按下確認才會帶上名字，避免誤送
            return jsonify(success=False, message='需要確認刪除'), 400
        try:
            count = delete_member(cfg, system.recognizer, name)
        except ValueError as e:
            return jsonify(success=False, message=f'{e}，無法刪除'), 400
        except Exception as e:                   # 訓練失敗等：照片可能已刪，名單已移除，回報給使用者
            logging.getLogger(__name__).exception('刪除成員 %s 失敗', name)
            return jsonify(success=False, message=f'刪除時發生錯誤：{e}'), 500
        bindings.set(name, None)                 # 之後同名的新成員不沿用舊手勢
        if storage is not None:
            storage.forget(name)
            storage.forget_attempts(name)
        system.emit('member_deleted', f'網頁刪除成員 {name}（{count} 張照片，已重新訓練）（{request.remote_addr}）',
                    name=name, photos=count, remote=request.remote_addr)
        masters = sum(1 for k in members().values() if k == 'master')
        return jsonify(success=True, message=f'已刪除 {name}', masters=masters)

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
    # 回報誤認
    # ==========================================
    def need_hardening():
        need_storage()
        if hardening is None:
            abort(404)

    @app.route('/api/attempts/<aid>/crop.jpg')
    def attempt_crop(aid):
        need_storage()
        att = storage.get_attempt(aid)
        if not att or not att['crop']:
            abort(404)
        path = storage.path(att['crop'])
        if not path.exists():
            abort(404)
        return send_file(path, mimetype='image/jpeg', max_age=3600)

    @app.route('/api/attempts/<aid>/report', methods=['POST'])
    def attempt_report(aid):
        """「這不是 某某」：收緊門檻 + 加入冒充者比對。每次都要 PIN。"""
        need_hardening()
        refused = pin_every_time(request.get_json(silent=True) or {})
        if refused:
            return refused
        try:
            r = hardening.report(aid, request.remote_addr)
        except KeyError:
            abort(404)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400
        msg = f'已回報：之後這張臉不會再被認成 {r["name"]}' if r['impostor_added'] else f'已收緊 {r["name"]} 的判定標準'
        return jsonify(success=True, message=msg, **r)

    @app.route('/api/attempts/<aid>/undo', methods=['POST'])
    def attempt_undo(aid):
        need_hardening()
        refused = pin_every_time(request.get_json(silent=True) or {})
        if refused:
            return refused
        try:
            r = hardening.undo(aid, request.remote_addr)
        except KeyError:
            abort(404)
        except ValueError as e:
            return jsonify(success=False, message=str(e)), 400
        return jsonify(success=True, message=f'已取消回報，{r["name"]} 的判定標準已重算', **r)

    @app.route('/api/users/<name>/threshold/reset', methods=['POST'])
    def user_threshold_reset(name):
        need_hardening()
        refused = pin_every_time(request.get_json(silent=True) or {})
        if refused:
            return refused
        if name not in members():
            abort(404)
        hardening.reset(name, request.remote_addr)
        return jsonify(success=True, message=f'{name} 的判定標準已恢復預設', threshold=cfg.threshold)

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
            aid = e['data'].get('attempt') if e['kind'] in ATTEMPT_EVENTS else None
            if aid:
                att = storage.get_attempt(aid)
                if att:
                    e['attempt'] = {'id': aid, 'name': att['name'], 'crop': bool(att['crop']),
                                    'reported': bool(att['reported']), 'distance': att['distance']}
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
        return Response(frames(), mimetype='multipart/x-mixed-replace; boundary=frame',
                        headers={'Cache-Control': 'no-store'})

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
        line_info = dict(line_info, basic_id=cfg.line_basic_id, target='user' if cfg.line_user_id else 'broadcast',
                         notify=notifier.status() if notifier is not None else None)
        snap = system.snapshot()
        return jsonify(
            line=line_info,
            storage=storage.usage().as_dict() if storage is not None else None,
            sentinel={'enabled': cfg.sentinel and storage is not None, 'trigger': cfg.sentinel_trigger,
                      'stop': cfg.sentinel_stop, 'max': cfg.sentinel_max, 'cooldown': cfg.sentinel_cooldown},
            system={'arduino': snap['arduino'], 'connected': snap['connected'],
                    'camera': system.info.get('camera', ''), 'gesture': system.info.get('gesture', ''),
                    'rule': ('人臉＋各自綁定的手勢（未綁定：' + ' / '.join(cfg.gesture_labels()) + '）')
                    if cfg.require_gesture else '只需人臉',
                    'unlock_seconds': cfg.unlock_seconds, 'version': __version__},
            security={'pin_required': store.required, 'source': getattr(store, 'source', 'env')},
            intercom={'device': intercom.name, 'channel': intercom.channel, 'clips': len(intercom.clips())}
            if intercom is not None else None,
        )

    return app
