"""回報誤認（「這不是 eric」）：只做反例，而且一定要主人確認過，不碰任何成員自己的照片。

原則：模型自己的判斷不能直接當成訓練答案，否則誤認會一路累積（資料污染，見 docs/05_資料污染與判定標準.md）。

  1. AttemptLog：每次 master 人臉通過記一筆嘗試 —— 人臉裁切圖、連續通過那幾幀的最小距離、
     最後結果（開鎖 / 手勢逾時 / 離開 / 開鎖失敗）
  2. Hardening.report(attempt)：主人在手機上回報（每次都要 PIN）
     A. 收緊那個人的門檻：比所有回報過的誤認距離都小 report_margin，最低 report_floor
     B. 把這張臉的 SFace 特徵加進冒充者清單（impostors/），立即生效：
        之後只要新的臉比任何成員照片都更像某張冒充者照片，一律當陌生人
     並用「手勢也通過而開鎖」的嘗試當作本人的距離分布，檢查新門檻會不會擋到本人
  3. undo：拿掉冒充者特徵、重算門檻（SFace 不用重新訓練）
  4. reset：門檻改回預設（例如在門口重新拍照 train 之後），冒充者清單保留
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import cv2

from .config import Config
from .events import Event, EventBus
from .face import read_thresholds, write_thresholds
from .storage import Storage

log = logging.getLogger(__name__)

CLOSE = {'unlock': 'unlocked', 'gesture_timeout': 'timeout', 'gesture_cancel': 'cancelled',
         'door_failed': 'door_failed', 'gesture_wrong': 'gesture_wrong'}


class AttemptLog:
    """訂閱事件，把每次人臉通過記成一筆嘗試。"""

    def __init__(self, cfg: Config, storage: Storage, bus: EventBus):
        self.cfg = cfg
        self.storage = storage
        self.dir = storage.root / 'attempts'
        self.dir.mkdir(parents=True, exist_ok=True)
        bus.subscribe('*', self.handle)

    def _save_crop(self, aid, crop) -> Optional[str]:
        if crop is None or getattr(crop, 'size', 0) == 0:
            return None
        rel = f'attempts/{aid}.jpg'
        if not cv2.imwrite(str(self.storage.path(rel)), crop, [cv2.IMWRITE_JPEG_QUALITY, 92]):
            return None
        return rel

    def handle(self, event: Event):
        aid = event.data.get('attempt')
        if not aid:
            return
        opens = event.kind == 'face_passed' or (event.kind == 'unlock' and '_crop' in event.data)  # 只用人臉時直接開鎖
        if opens:
            crop = self._save_crop(aid, event.data.get('_crop'))
            self.storage.add_attempt(aid, event.data.get('name', ''), event.data.get('distance'), crop,
                                     'unlocked' if event.kind == 'unlock' else 'pending')
        elif event.kind in CLOSE:
            self.storage.set_attempt(aid, outcome=CLOSE[event.kind])


class Hardening:
    """處理「這不是我」回報：收緊門檻（A）＋ 冒充者特徵（B）。"""

    def __init__(self, cfg: Config, storage: Storage, recognizer, bus: EventBus):
        self.cfg = cfg
        self.storage = storage
        self.recognizer = recognizer
        self.bus = bus

    # ---- 門檻 ----
    def threshold(self, name: str) -> float:
        fn = getattr(self.recognizer, 'threshold_for', None)
        if fn:
            return fn(name)
        return min(read_thresholds(self.cfg.thresholds_file).get(name, self.cfg.threshold), self.cfg.threshold)

    def _set(self, name, value):
        """寫檔 + 換掉執行中的門檻。value=None 表示改回預設。"""
        table = read_thresholds(self.cfg.thresholds_file)
        live = getattr(self.recognizer, 'thresholds', None)
        if value is None:
            table.pop(name, None)
            if live is not None:
                live.pop(name, None)
        else:
            table[name] = value
            if live is not None:
                live[name] = value
        write_thresholds(self.cfg.thresholds_file, table)

    def _target(self, impostor_distances) -> float:
        cfg = self.cfg
        if not impostor_distances:
            return cfg.threshold
        return round(max(cfg.report_floor, min(cfg.threshold, min(impostor_distances) - cfg.report_margin)), 1)

    @staticmethod
    def _key(name, aid):
        return f'{name}_{aid}'

    # ---- 回報 ----
    def report(self, aid: str, remote: str = '') -> dict:
        att = self.storage.get_attempt(aid)
        if att is None:
            raise KeyError(aid)
        if att['reported']:
            raise ValueError('這筆已經回報過了')
        name, dist = att['name'], att['distance']

        # B. 冒充者特徵：裁切圖另存到 impostors/（重新啟動時會重新載入），並立刻加進辨識器
        added = False
        if att['crop']:
            src = self.storage.path(att['crop'])
            img = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE) if src.exists() else None
            engine = getattr(self.recognizer, 'engine', None)
            vec = engine.embed_photo(img) if img is not None and engine is not None else None
            if vec is not None:
                self.cfg.impostor_dir.mkdir(exist_ok=True)
                key = self._key(name, aid)
                cv2.imwrite(str(self.cfg.impostor_dir / f'{key}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                self.recognizer.add_impostor(key, vec)
                added = True
        self.storage.set_attempt(aid, reported=1)

        # A. 收緊門檻：只會變嚴，不會因為這次回報變鬆
        before = self.threshold(name)
        impostor = self.storage.attempt_distances(name, reported=True)
        after = min(before, self._target(impostor))
        if after < before:
            self._set(name, after)

        # 本人會不會也被擋：用「手勢也通過而開鎖」的嘗試當作本人的距離
        genuine = self.storage.attempt_distances(name, reported=False, outcome='unlocked')
        blocked = sum(1 for d in genuine if d >= after)
        warn = None
        if genuine and blocked / len(genuine) > 0.2:
            warn = (f'新門檻會擋掉 {name} 本人約 {blocked * 100 // len(genuine)}% 的開鎖紀錄，'
                    f'建議在門口光線下重新拍照（capture）後 train，再到成員頁恢復預設判定標準')
        elif dist is not None and dist - self.cfg.report_margin <= self.cfg.report_floor:
            warn = (f'這張臉跟 {name} 非常像（距離 {dist:.0f}），門檻已收到下限，主要靠冒充者比對擋下。'
                    f'如果其實是 {name} 本人，請按「取消回報」，否則 {name} 也會被擋')
        if not added:
            warn = (warn + '；' if warn else '') + '這筆沒有可用的人臉圖（可能已過保存期限），只收緊了門檻'

        text = (f'回報誤認：有人被認成 {name}（距離 {dist:.0f}）；門檻 {before:.0f} → {after:.0f}'
                f'{"，已加入冒充者比對" if added else ""}（{remote}）' if dist is not None else
                f'回報誤認：有人被認成 {name}（{remote}）')
        self.bus.publish(Event('impostor_reported', text, {'name': name, 'attempt': aid, 'before': before,
                                                           'after': after, 'remote': remote}))
        return {'name': name, 'distance': dist, 'before': before, 'after': after, 'impostor_added': added,
                'reports': len(impostor), 'warning': warn}

    def undo(self, aid: str, remote: str = '') -> dict:
        """取消回報（例如不小心把本人回報成冒充者）：拿掉冒充者特徵、依剩下的回報重算門檻。"""
        att = self.storage.get_attempt(aid)
        if att is None:
            raise KeyError(aid)
        if not att['reported']:
            raise ValueError('這筆沒有回報過')
        name = att['name']
        key = self._key(name, aid)
        (self.cfg.impostor_dir / f'{key}.jpg').unlink(missing_ok=True)
        removed = bool(getattr(self.recognizer, 'remove_impostor', lambda k: False)(key))
        self.storage.set_attempt(aid, reported=0)
        impostor = self.storage.attempt_distances(name, reported=True)
        after = self._target(impostor)
        self._set(name, after if impostor else None)
        self.bus.publish(Event('impostor_undone', f'取消回報：{name} 的誤認紀錄已移除，門檻 → {after:.0f}（{remote}）',
                               {'name': name, 'attempt': aid, 'remote': remote}))
        return {'name': name, 'after': after, 'impostor_removed': removed}

    def reset(self, name: str, remote: str = '') -> float:
        """門檻改回預設。冒充者照片保留（它們本來就不是這個人）。"""
        before = self.threshold(name)
        self._set(name, None)
        self.bus.publish(Event('threshold_reset', f'{name} 的判定標準恢復預設：門檻 {before:.0f} → '
                               f'{self.cfg.threshold:.0f}（{remote}）', {'name': name, 'remote': remote}))
        return self.cfg.threshold

    def info(self, name: str) -> dict:
        return {'threshold': self.threshold(name), 'default': self.cfg.threshold,
                'reports': len(self.storage.attempt_distances(name, reported=True))}
