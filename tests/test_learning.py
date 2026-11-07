"""回報誤認（SFace 版）：嘗試紀錄、收緊門檻、冒充者特徵、取消回報、恢復預設。

用真的 FaceRecognizer + 假的人臉引擎（conftest 的 FakeFaceEngine：特徵 = 縮小的像素），
「長得像 eric 的人」= eric 的照片混一些雜訊，距離落在門檻以內，會被誤認成 eric。
"""
import json

import cv2
import numpy as np
import pytest

from smartlock.config import Config
from smartlock.face import FaceRecognizer, delete_member, read_map, read_thresholds, train, write_thresholds
from smartlock.learning import AttemptLog, Hardening
from smartlock.storage import Storage

from conftest import DT, FakeRecognizer, Harness

PIN = '4321'


def people(cfg, names=('eric', 'amy', 'kevin'), seed=7):
    cfg.face_dir.mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    imgs = {}
    for name in names:
        imgs[name] = rng.integers(0, 255, (160, 160), dtype=np.uint8)
        for i in range(3):
            cv2.imwrite(str(cfg.face_dir / f'{name}_{i}.png'), imgs[name])
    train(cfg)
    return imgs


def lookalike(base, mix, seed=11):
    """base 混入 mix 比例的雜訊。"""
    noise = np.random.default_rng(seed).integers(0, 255, base.shape, dtype=np.uint8)
    return cv2.addWeighted(base, 1 - mix, noise, mix, 0)


def mix_for(rec, base, lo, hi):
    """找一個混合比例，讓距離落在 (lo, hi) 之間（被認成 base 那個人、但不是一模一樣）。"""
    for mix in np.linspace(0.05, 0.9, 120):
        img = lookalike(base, mix)
        d = rec.identify(img)[0].distance
        if lo < d < hi:
            return img, d
    raise AssertionError('找不到合適的混合比例')


class Rig:
    """真的辨識器 + 狀態機 + 紀錄 + 網頁。"""

    def __init__(self, tmp_path, pin=PIN):
        self.h = Harness(Config(base=tmp_path, control_pin=pin))
        self.cfg = self.h.cfg
        self.imgs = people(self.cfg)
        self.rec = FaceRecognizer.load(self.cfg)
        self.h.system.recognizer = self.rec
        self.storage = Storage(self.cfg)
        self.h.system.bus.subscribe('*', self.storage.log_event)
        AttemptLog(self.cfg, self.storage, self.h.system.bus)
        self.hard = Hardening(self.cfg, self.storage, self.rec, self.h.system.bus)
        from smartlock.web import create_web
        self.c = create_web(self.h.system, self.storage, hardening=self.hard).test_client()

    def frames(self, img, seconds, gesture=None):
        g = self.h.gesture
        g.name, g.score = gesture, 0.93
        frame = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        for _ in range(round(seconds / DT)):
            self.h.system.process_frame(frame.copy())
            self.h.clock.t += DT

    def leave(self):
        self.h.system.recognizer = FakeRecognizer()          # 鏡頭沒有臉 3 秒以上 → 重新允許自動開鎖
        for _ in range(round(4 / DT)):
            self.h.system.process_frame(np.zeros((160, 160, 3), np.uint8))
            self.h.clock.t += DT
        self.h.system.recognizer = self.rec

    def attempt(self, img):
        """走到門口 → 比讚 → 離開。回傳這次有沒有開鎖。"""
        before = self.h.unlocks
        self.frames(img, 1.0)
        self.frames(img, 1.0, gesture='Thumb_Up')
        self.leave()
        return self.h.unlocks > before

    def last_attempt(self):
        aid = [e.data['attempt'] for e in self.h.events if e.kind == 'face_passed'][-1]
        return self.storage.get_attempt(aid)

    def post(self, url, **body):
        return self.c.post(url, json=dict(body))


# ==========================================
# 辨識器：冒充者特徵、個人門檻
# ==========================================
def test_impostor_feature_blocks_only_the_lookalike(tmp_path):
    cfg = Config(base=tmp_path)
    imgs = people(cfg)
    rec = FaceRecognizer.load(cfg)
    fake, d = mix_for(rec, imgs['eric'], 20, 45)
    assert rec.identify(fake)[0].is_master                          # 原本會被誤認成 eric
    rec.add_impostor('eric_x', rec.engine.feature(fake))
    r = rec.identify(fake)[0]
    assert r.impostor and not r.known and not r.is_master
    assert rec.identify(imgs['eric'])[0].is_master                  # eric 本人比冒充者照片更像自己的相簿
    assert rec.identify(imgs['amy'])[0].known
    assert rec.remove_impostor('eric_x') and rec.identify(fake)[0].is_master


def test_personal_threshold_only_gets_stricter(tmp_path):
    cfg = Config(base=tmp_path)
    people(cfg)
    write_thresholds(cfg.thresholds_file, {'eric': 30.0, 'amy': 80.0})
    rec = FaceRecognizer.load(cfg)
    assert rec.threshold_for('eric') == 30.0
    assert rec.threshold_for('amy') == cfg.threshold                 # 檔案寫得比預設鬆也不採用
    assert rec.threshold_for('kevin') == cfg.threshold


def test_lbph_era_thresholds_file_is_ignored(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.thresholds_file.write_text(json.dumps({'eric': 60.0}))      # LBPH 版的格式（沒有 scale 標記）
    assert read_thresholds(cfg.thresholds_file) == {}
    write_thresholds(cfg.thresholds_file, {'eric': 42.0})
    assert json.loads(cfg.thresholds_file.read_text())['scale'] == 'sface'
    assert read_thresholds(cfg.thresholds_file) == {'eric': 42.0}


# ==========================================
# 嘗試紀錄
# ==========================================
def test_attempt_logged_with_crop_and_outcome(tmp_path):
    rig = Rig(tmp_path)
    assert rig.attempt(rig.imgs['eric'])
    att = rig.last_attempt()
    assert att['name'] == 'eric' and att['outcome'] == 'unlocked' and att['reported'] == 0
    assert att['distance'] is not None and att['distance'] < 5
    assert rig.storage.path(att['crop']).exists()
    unlock = [e for e in rig.storage.list_events() if e['kind'] == 'unlock'][0]
    assert unlock['data']['attempt'] == att['id'] and '_crop' not in unlock['data']   # 影像不寫進資料庫


def test_attempt_timeout_outcome(tmp_path):
    rig = Rig(tmp_path)
    rig.frames(rig.imgs['eric'], rig.cfg.gesture_window + 1.5)      # 不比手勢
    assert rig.last_attempt()['outcome'] == 'timeout'


def test_gesture_wrong_is_an_attempt_on_the_timeline(tmp_path):
    """人臉過了但手勢錯 = 可能是冒充者：記成一筆嘗試，時間軸上可以回報。"""
    rig = Rig(tmp_path)
    rig.h.system.bindings.set('eric', 'ILoveYou')       # 預設的讚就成了錯的手勢
    rig.frames(rig.imgs['eric'], 1.0)
    rig.frames(rig.imgs['eric'], 1.0, gesture='Thumb_Up')
    assert rig.last_attempt()['outcome'] == 'gesture_wrong'
    rig.post('/api/login', pin=PIN)
    ev = rig.c.get('/api/events').get_json()['events']
    wrong = next(e for e in ev if e['kind'] == 'gesture_wrong')
    assert wrong['attempt']['name'] == 'eric' and not wrong['attempt']['reported']
    assert rig.post(f"/api/attempts/{wrong['attempt']['id']}/report", pin=PIN).get_json()['success']


def test_attempt_crops_expire_but_reported_rows_stay(tmp_path):
    rig = Rig(tmp_path)
    rig.attempt(rig.imgs['eric'])
    rig.attempt(rig.imgs['amy'])                                    # amy 也是 master
    a, b = [rig.storage.get_attempt(e.data['attempt']) for e in rig.h.events if e.kind == 'face_passed']
    rig.storage.set_attempt(b['id'], reported=1)
    rig.storage.wallclock = lambda: 10 ** 10                        # 很久以後
    rig.storage.enforce()
    assert rig.storage.get_attempt(a['id']) is None                 # 一般嘗試整筆過期
    kept = rig.storage.get_attempt(b['id'])
    assert kept and kept['crop'] is None                            # 回報過的留距離（門檻靠它算），圖刪掉
    assert not list((rig.storage.root / 'attempts').glob('*.jpg'))


# ==========================================
# 回報 → 收緊 + 冒充者；取消回報；恢復預設
# ==========================================
def test_report_end_to_end(tmp_path):
    rig = Rig(tmp_path)
    fake, d = mix_for(rig.rec, rig.imgs['eric'], 40, 49)
    assert rig.attempt(rig.imgs['eric'])                            # 本人
    assert rig.attempt(fake)                                        # 誤認：冒充者開了門
    att = rig.last_attempt()
    rig.post('/api/login', pin=PIN)                                 # 看時間軸本來就要登入；回報另外每次要 PIN
    r = rig.post(f'/api/attempts/{att["id"]}/report', pin=PIN).get_json()
    assert r['success'] and r['impostor_added'] and r['name'] == 'eric'
    expected = round(max(rig.cfg.report_floor, att['distance'] - rig.cfg.report_margin), 1)
    assert r['after'] == expected < r['before'] == rig.cfg.threshold
    assert read_thresholds(rig.cfg.thresholds_file) == {'eric': expected}
    assert rig.rec.threshold_for('eric') == expected                # 不用重新啟動
    assert list(rig.cfg.impostor_dir.glob('eric_*.jpg'))
    assert 'impostor_reported' in rig.h.kinds()

    assert not rig.attempt(fake)                                    # 同一個人再來：開不了
    assert rig.attempt(rig.imgs['eric'])                            # 本人照常
    assert rig.post(f'/api/attempts/{att["id"]}/report', pin=PIN).status_code == 400   # 不能重複回報

    # 重新啟動：門檻和冒充者照片都還在
    again = FaceRecognizer.load(rig.cfg)
    assert again.threshold_for('eric') == expected and again.impostor_count == 1
    assert not again.identify(fake)[0].known

    # 取消回報：冒充者拿掉、門檻回到預設
    u = rig.post(f'/api/attempts/{att["id"]}/undo', pin=PIN).get_json()
    assert u['success'] and u['impostor_removed']
    assert rig.rec.threshold_for('eric') == rig.cfg.threshold and rig.rec.impostor_count == 0
    assert not list(rig.cfg.impostor_dir.glob('*.jpg'))
    assert rig.attempt(fake)


def test_report_needs_pin_every_time(tmp_path):
    rig = Rig(tmp_path)
    rig.attempt(rig.imgs['eric'])
    aid = rig.last_attempt()['id']
    assert rig.c.get(f'/api/attempts/{aid}/crop.jpg').status_code == 401
    assert rig.post('/api/login', pin=PIN).status_code == 200
    assert rig.c.get(f'/api/attempts/{aid}/crop.jpg').status_code == 200
    r = rig.post(f'/api/attempts/{aid}/report')
    assert r.status_code == 403 and r.get_json()['message'] == '需要輸入 PIN'      # 已登入也不算
    assert rig.post(f'/api/attempts/{aid}/report', pin='0000').status_code == 403
    assert rig.storage.get_attempt(aid)['reported'] == 0
    assert rig.post('/api/attempts/nope/report', pin=PIN).status_code == 404
    assert rig.post(f'/api/attempts/{aid}/undo', pin=PIN).status_code == 400       # 沒回報過


def test_report_on_near_identical_face_hits_floor_and_warns(tmp_path):
    rig = Rig(tmp_path)
    rig.attempt(rig.imgs['eric'])                                   # 距離幾乎是 0（「其實是本人」的情況）
    r = rig.hard.report(rig.last_attempt()['id'])
    assert r['after'] == rig.cfg.report_floor
    assert r['warning'] and '取消回報' in r['warning']


def test_report_warns_when_owner_would_be_blocked(tmp_path):
    rig = Rig(tmp_path)
    for i, d in enumerate((40.0, 41.0, 42.0, 43.0)):                # eric 本人平常的距離（手勢也通過才算本人）
        rig.storage.add_attempt(f'g{i}', 'eric', d, None, 'unlocked')
    rig.storage.add_attempt('bad', 'eric', 41.5, None, 'unlocked')
    r = rig.hard.report('bad')
    assert r['after'] == 38.5 and not r['impostor_added']
    assert '擋掉 eric 本人' in r['warning'] and '人臉圖' in r['warning']


def test_threshold_follows_the_closest_report_and_undo_recomputes(tmp_path):
    rig = Rig(tmp_path)
    rig.storage.add_attempt('a', 'eric', 47.0, None, 'unlocked')
    rig.storage.add_attempt('b', 'eric', 44.0, None, 'unlocked')
    assert rig.hard.report('a')['after'] == 44.0
    assert rig.hard.report('b')['after'] == 41.0
    assert rig.hard.undo('b')['after'] == 44.0                      # 剩下 a
    assert rig.rec.threshold_for('eric') == 44.0
    assert rig.hard.undo('a')['after'] == rig.cfg.threshold
    assert read_thresholds(rig.cfg.thresholds_file) == {}


def test_reset_threshold_via_web(tmp_path):
    rig = Rig(tmp_path)
    rig.storage.add_attempt('a', 'eric', 45.0, None, 'unlocked')
    rig.hard.report('a')
    rig.post('/api/login', pin=PIN)
    u = {x['name']: x for x in rig.c.get('/api/users').get_json()['users']}
    assert u['eric']['threshold'] == 42.0 and u['eric']['reports'] == 1
    assert rig.post('/api/users/eric/threshold/reset').status_code == 403       # 每次都要 PIN
    assert rig.post('/api/users/eric/threshold/reset', pin=PIN).get_json()['success']
    assert rig.rec.threshold_for('eric') == rig.cfg.threshold
    assert 'threshold_reset' in rig.h.kinds()


def test_timeline_events_carry_attempt(tmp_path):
    rig = Rig(tmp_path)
    rig.attempt(rig.imgs['eric'])
    rig.post('/api/login', pin=PIN)
    ev = rig.c.get('/api/events').get_json()['events']
    unlock = next(e for e in ev if e['kind'] == 'unlock')
    assert unlock['attempt']['name'] == 'eric' and unlock['attempt']['crop'] and not unlock['attempt']['reported']
    assert 'attempt' not in next(e for e in ev if e['kind'] == 'face_passed')   # 一次嘗試只在結果那筆顯示


def test_delete_member_clears_threshold_and_attempts(tmp_path):
    rig = Rig(tmp_path)
    rig.storage.add_attempt('a', 'amy', 45.0, None, 'unlocked')
    rig.hard.report('a')
    assert read_thresholds(rig.cfg.thresholds_file) == {'amy': 42.0}
    rig.post('/api/login', pin=PIN)
    assert rig.post('/api/users/amy/delete', pin=PIN, confirm='amy').status_code == 200
    assert read_thresholds(rig.cfg.thresholds_file) == {}
    assert rig.storage.get_attempt('a') is None and 'amy' not in rig.rec.thresholds
