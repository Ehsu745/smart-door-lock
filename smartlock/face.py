"""人臉：YuNet 偵測 + SFace 特徵比對、採集（capture）、登錄（train）。

做法：
  - SFace 是已經訓練好的神經網路，把一張對齊過的臉轉成 128 維特徵向量（同一人會靠近、不同人會遠離）
  - train 不訓練任何東西：把 faces/ 每張照片轉成特徵，存成 face_feats.npz（每張 512 bytes）
  - 辨識：新的臉跟所有已登錄的特徵算 cosine 相似度，取最像的一張
  - 對外的 distance =（1 − cosine）× 100，越小越像，沿用 LBPH 時代「距離 < 門檻算認得」的介面
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import FACE_MODELS, Config

log = logging.getLogger(__name__)


# ==========================================
# 模型檔（第一次使用自動下載到 models/）
# ==========================================
def _download(url: str, path: Path, sha256: str) -> None:
    import ssl
    import urllib.request
    try:                                   # macOS 的 python.org 版 Python 常缺根憑證，改用 certifi
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    tmp = path.with_suffix('.part')
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=60) as resp, open(tmp, 'wb') as out:
            shutil.copyfileobj(resp, out)
        if _sha256(tmp) != sha256:
            raise RuntimeError('檔案檢查碼不符')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def ensure_face_models(cfg: Config) -> None:
    for name, (url, sha256) in FACE_MODELS.items():
        path = cfg.models_dir / name
        if path.exists():
            continue
        path.parent.mkdir(exist_ok=True)
        log.info('下載人臉模型 %s ...', name)
        try:
            _download(url, path, sha256)
        except Exception as exc:
            raise RuntimeError(f'人臉模型 {name} 下載失敗（{exc}）。可以手動下載：curl -L -o {path} {url}')


class FaceEngine:
    """包住 OpenCV 的 YuNet（找臉 + 5 個五官點）與 SFace（128 維特徵）。測試時可換成假的。"""

    def __init__(self, cfg: Config):
        ensure_face_models(cfg)
        detector_file, recognizer_file = (str(cfg.models_dir / name) for name in FACE_MODELS)
        try:
            self.det = cv2.FaceDetectorYN.create(detector_file, '', (320, 320), cfg.face_score, 0.3, 5000)
            self.rec = cv2.FaceRecognizerSF.create(recognizer_file, '')
        except (AttributeError, cv2.error) as exc:
            raise RuntimeError(f'OpenCV {cv2.__version__} 無法載入 YuNet / SFace（需要 4.8 以上）：{exc}')
        self._size = None
        self._lock = threading.Lock()       # 同一個 OpenCV 網路物件不能被兩個執行緒同時用

    def detect(self, bgr) -> np.ndarray:
        """回傳 N×15：x, y, w, h, 5 個五官點 (x, y)，最後一欄是信心值。沒有臉回傳空陣列。"""
        h, w = bgr.shape[:2]
        with self._lock:
            if self._size != (w, h):
                self.det.setInputSize((w, h))
                self._size = (w, h)
            _, faces = self.det.detect(bgr)
        return np.empty((0, 15), np.float32) if faces is None else faces

    def feature(self, bgr, face_row) -> np.ndarray:
        """依五官點對齊成 112×112 → 128 維單位向量。"""
        with self._lock:
            feat = self.rec.feature(self.rec.alignCrop(bgr, face_row)).ravel()
        return feat / max(float(np.linalg.norm(feat)), 1e-9)

    def embed_photo(self, gray) -> Optional[np.ndarray]:
        """faces/ 裡已裁好的臉 → 特徵。舊版 Haar 裁得很緊，先補邊讓 YuNet 看得到整張臉。"""
        pad = max(gray.shape[:2]) // 3
        img = cv2.cvtColor(cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_REPLICATE), cv2.COLOR_GRAY2BGR)
        faces = self.detect(img)
        if len(faces) == 0:
            return None
        return self.feature(img, max(faces, key=lambda f: f[2] * f[3]))


def make_engine(cfg: Config) -> FaceEngine:
    """建立 FaceEngine。測試會把這個函式換掉，所以其他地方一律透過它建立。"""
    return FaceEngine(cfg)


# ==========================================
# 名單（id_map.txt）
# ==========================================
def read_map(path: Path) -> Dict[int, Tuple[str, str]]:
    """讀 id_map.txt，回傳 {編號: (名字, 類別)}。"""
    result = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            parts = line.strip().split(':')
            if len(parts) == 3 and parts[0].isdigit():
                result[int(parts[0])] = (parts[1], parts[2])
    return result


ROLES = ('master', 'guest')
PHOTO_EXTS = ('.jpg', '.jpeg', '.png')


def write_map(path: Path, mapping: Dict[int, Tuple[str, str]]) -> None:
    """原子寫入 id_map.txt：先寫暫存檔再取代，寫到一半斷電也不會壞掉。"""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(''.join(f'{num}:{name}:{kind}\n' for num, (name, kind) in sorted(mapping.items())), encoding='utf-8')
    tmp.replace(path)


def set_role(cfg: Config, recognizer, name: str, role: str) -> str:
    """把某人改成 master / guest，立即生效（不用重新登錄：特徵只對應編號，身分只存在 id_map.txt）。
    回傳原本的身分。之後重新 train 也會保留這個設定。"""
    if role not in ROLES:
        raise ValueError(f'身分只能是 {" / ".join(ROLES)}')
    mapping = read_map(cfg.map_file)
    nums = [num for num, (n, _) in mapping.items() if n == name]
    if not nums:
        raise KeyError(name)
    old = mapping[nums[0]][1]
    for num in nums:
        mapping[num] = (name, role)
    write_map(cfg.map_file, mapping)
    if recognizer is not None:
        recognizer.mapping = mapping            # 換掉整個 dict：辨識執行緒下一幀就用新身分
    return old


_retrain_lock = threading.Lock()


def photos_of(cfg: Config, name: str) -> List[Path]:
    """faces/ 裡屬於 name 的照片（檔名 <名字>_*.jpg / .jpeg / .png，和 train 的規則一致）。"""
    if not cfg.face_dir.exists():
        return []
    return sorted(p for p in cfg.face_dir.iterdir()
                  if p.suffix.lower() in PHOTO_EXTS and p.stem.split('_')[0] == name)


def delete_member(cfg: Config, recognizer, name: str) -> int:
    """刪除某人：照片、名單、特徵全部移除，無法復原。回傳刪掉的照片張數。

    順序：
      1. 先把他從名單拿掉 → 辨識執行緒下一幀就把他當陌生人（還在用舊特徵也一樣）
      2. 刪照片
      3. 用剩下的照片重新登錄，face_feats.npz 裡不再有他的特徵
      4. 新特徵與新名單一起換上（編號會重排，所以兩個要同時換）
    至少要保留一位成員。"""
    with _retrain_lock:
        mapping = read_map(cfg.map_file)
        names = {n for n, _ in mapping.values()}
        if name not in names:
            raise KeyError(name)
        if len(names) <= 1:
            raise ValueError('至少要保留一位成員')
        remaining = {num: v for num, v in mapping.items() if v[0] != name}
        write_map(cfg.map_file, remaining)
        if recognizer is not None:
            recognizer.mapping = remaining
        files = photos_of(cfg, name)
        for path in files:
            path.unlink()
        train(cfg)
        if isinstance(recognizer, FaceRecognizer):
            recognizer.swap(load_gallery(cfg), read_map(cfg.map_file))
        log.info('已刪除成員 %s（%d 張照片），特徵已重新登錄', name, len(files))
        return len(files)


# ==========================================
# 辨識
# ==========================================
@dataclass
class FaceResult:
    box: Tuple[int, int, int, int]      # x, y, w, h
    label: int
    distance: float                      # (1 − cosine) × 100，越小越像
    person: str                          # id_map 裡的名字；不在名單上是 'Unknown'
    kind: str                            # master / guest / Stranger
    known: bool                          # 距離低於門檻，且身分是 master 或 guest

    @property
    def is_master(self) -> bool:
        return self.known and self.kind == 'master'

    @property
    def is_guest(self) -> bool:
        return self.known and self.kind == 'guest'


Gallery = Tuple[np.ndarray, np.ndarray]   # (N×128 特徵, N 個編號)


def load_gallery(cfg: Config) -> Gallery:
    with np.load(cfg.feature_file) as data:
        return data['feats'].astype(np.float32), data['labels'].astype(np.int32)


class FaceRecognizer:
    """把「找臉 + 認人」包成一個介面：identify(gray) → [FaceResult]。測試時可以換成假的。"""

    def __init__(self, cfg: Config, gallery: Gallery, mapping, engine=None):
        self.cfg = cfg
        self.gallery = gallery
        self.mapping = mapping
        self.engine = engine or make_engine(cfg)
        self._lock = threading.Lock()

    def swap(self, gallery: Gallery, mapping) -> None:
        """同時換上新特徵與新名單（重新登錄後編號會重排，分開換會有一幀認錯人）。"""
        with self._lock:
            self.gallery, self.mapping = gallery, mapping

    @classmethod
    def load(cls, cfg: Config) -> 'FaceRecognizer':
        engine = make_engine(cfg)
        if not cfg.feature_file.exists():
            # 從 LBPH 版升級：直接用 faces/ 既有照片算特徵，使用者不用重拍、也不用手動 train
            if not any(p.suffix.lower() in PHOTO_EXTS for p in cfg.face_dir.glob('*')):
                raise RuntimeError('faces/ 沒有照片，請先 capture 再 train')
            log.info('找不到 %s，用 faces/ 的照片建立人臉特徵...', cfg.feature_file.name)
            train(cfg, engine)
        return cls(cfg, load_gallery(cfg), read_map(cfg.map_file), engine)

    def identify(self, gray) -> List[FaceResult]:
        with self._lock:
            (feats, labels), mapping = self.gallery, self.mapping
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)      # 登錄照片是灰階，辨識也用灰階，兩邊條件一致
        fh, fw = gray.shape[:2]
        results = []
        for row in self.engine.detect(bgr):
            x, y = max(int(row[0]), 0), max(int(row[1]), 0)
            w, h = min(int(row[2]), fw - x), min(int(row[3]), fh - y)
            if w < self.cfg.min_face[0] or h <= 0:
                continue
            sims = feats @ self.engine.feature(bgr, row)
            best = int(np.argmax(sims))
            label, distance = int(labels[best]), (1.0 - float(sims[best])) * 100
            person, kind = mapping.get(label, ('Unknown', 'Stranger'))
            known = distance < self.cfg.threshold and kind in ROLES
            results.append(FaceResult((x, y, w, h), label, distance, person, kind, known))
        return results


# ==========================================
# 採集與登錄
# ==========================================
def capture(cfg: Config, user_id: str, count: int = None) -> int:
    """拍 count 張人臉存到 faces/；有螢幕時顯示預覽與進度條。回傳實際拍到的張數。"""
    from .camera import Camera, FrameReader
    from .display import draw_bar, draw_label, has_display

    count = count or cfg.capture_count
    if not re.fullmatch(r'[A-Za-z0-9-]+', user_id or ''):
        raise ValueError('ID 只能用英文、數字、減號（不可含底線或空格）')
    engine = make_engine(cfg)
    cfg.face_dir.mkdir(exist_ok=True)
    stamp = time.strftime('%Y%m%d%H%M%S')        # 每次採集不同檔名，不會蓋掉舊照片
    show = has_display()
    camera = Camera(cfg)
    reader = FrameReader(camera)
    saved, last_save, just_saved_until = 0, 0.0, 0.0
    log.info('開始採集 %s：請正對鏡頭並稍微轉動角度%s', user_id, '（按 q 結束）' if show else '')
    try:
        while saved < count:
            frame = reader.read()
            if frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = [tuple(int(v) for v in f[:4]) for f in engine.detect(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
                     if f[2] >= cfg.min_face[0]]
            if len(faces) == 1 and time.monotonic() - last_save >= 0.2:
                x, y, w, h = faces[0]
                m = w // 4                                       # 多留一圈邊，登錄時才找得到五官
                crop = gray[max(y - m, 0):y + h + m, max(x - m, 0):x + w + m]
                if crop.size and cv2.imwrite(str(cfg.face_dir / f'{user_id}_{stamp}-{saved}.jpg'), crop):
                    saved += 1
                    last_save = time.monotonic()
                    just_saved_until = last_save + 0.12
                    print(f'\r已採集 {saved}/{count}', end='', flush=True)
            if show:
                one = len(faces) == 1
                for x, y, w, h in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0) if one else (0, 0, 255), 2)
                if just_saved_until > time.monotonic() and one:          # 剛拍到時框線閃白
                    x, y, w, h = faces[0]
                    cv2.rectangle(frame, (x - 4, y - 4), (x + w + 4, y + h + 4), (255, 255, 255), 2)
                hint = ('Turn your head slowly' if one else
                        'No face - look at the camera' if len(faces) == 0 else 'Only ONE face please')
                draw_label(frame, f'Capturing: {user_id}', (12, 30), 0.75)
                draw_label(frame, hint, (12, 62), 0.6, (255, 255, 255), (0, 120, 0) if one else (0, 0, 160))
                fh, fw = frame.shape[:2]
                draw_bar(frame, 12, fh - 40, fw - 24, 22, saved / count, (0, 200, 0))
                draw_label(frame, f'{saved}/{count}', (fw // 2 - 30, fh - 50), 0.7)
                cv2.imshow('Capture (q = quit)', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    finally:
        camera.close()
        if show:
            cv2.destroyAllWindows()
    print()
    if show and saved >= count:                       # 最後一張停留一下，讓人看到 30/30
        time.sleep(0.3)
    log.info('採集完成：%d 張。接著執行 train 登錄特徵', saved)
    return saved


def train(cfg: Config, engine=None) -> Dict[str, int]:
    """把 faces/ 每張照片轉成特徵，寫出 face_feats.npz 與 id_map.txt。回傳每個人可用的照片張數。

    不是重新訓練神經網路（SFace 已訓練好），只是「登錄」：存下每張照片的 128 維特徵。"""
    if not cfg.face_dir.exists():
        raise RuntimeError('沒有 faces 資料夾，請先採集')
    engine = engine or make_engine(cfg)              # 自己建一份，不和辨識執行緒搶同一個網路物件
    old_category = {name: cat for name, cat in read_map(cfg.map_file).values()}
    feats, ids, labels, per_person, skipped = [], [], {}, {}, {}
    for path in sorted(cfg.face_dir.iterdir()):
        if path.suffix.lower() not in PHOTO_EXTS:
            continue
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            continue
        name = path.stem.split('_')[0]
        num = labels.setdefault(name, len(labels))   # 編號規則和 LBPH 版相同：依檔名排序第一次出現的順序
        per_person.setdefault(name, 0)
        vec = engine.embed_photo(img)
        if vec is None:
            skipped[name] = skipped.get(name, 0) + 1
            continue
        feats.append(vec)
        ids.append(num)
        per_person[name] += 1
    if not feats:
        raise RuntimeError('faces 資料夾沒有可用的照片（找不到臉），請重新採集')
    tmp = cfg.feature_file.with_suffix('.tmp')
    with open(tmp, 'wb') as f:
        np.savez(f, feats=np.vstack(feats).astype(np.float32), labels=np.asarray(ids, np.int32))
    tmp.replace(cfg.feature_file)
    # 保留手動改過的 guest 設定，新的人預設 master
    write_map(cfg.map_file, {num: (name, old_category.get(name, 'master')) for name, num in labels.items()})
    log.info('登錄完成：共 %d 張 → %s', len(feats), '、'.join(f'{n} {c} 張' for n, c in per_person.items()))
    for name, n in skipped.items():
        log.warning('  %s 有 %d 張照片找不到五官，已略過%s', name, n, '（全部失敗，這個人認不出來，請重拍）'
                    if per_person[name] == 0 else '')
    return per_person
