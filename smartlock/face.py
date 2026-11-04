"""人臉：Haar 偵測 + LBPH 辨識、採集（capture）、訓練（train）。"""
from __future__ import annotations

import glob
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

from .config import Config

log = logging.getLogger(__name__)

HAAR_NAME = 'haarcascade_frontalface_default.xml'


def load_detector(cfg: Config):
    candidates = [cfg.base / HAAR_NAME]
    if hasattr(cv2, 'data'):    # pip 版 OpenCV 才有 cv2.data
        candidates.append(Path(cv2.data.haarcascades) / HAAR_NAME)
    candidates += [Path(p) for p in glob.glob(f'/usr/share/opencv*/haarcascades/{HAAR_NAME}')]
    for path in candidates:
        if path.exists():
            detector = cv2.CascadeClassifier(str(path))
            if not detector.empty():
                return detector
    raise RuntimeError(f'找不到 {HAAR_NAME}：把它放到專案資料夾，或 sudo apt install opencv-data')


def new_recognizer():
    if not hasattr(cv2, 'face'):
        raise RuntimeError('OpenCV 缺少 cv2.face：請安裝 opencv-contrib-python（mediapipe 會自動安裝）')
    return cv2.face.LBPHFaceRecognizer_create()


def normalize(face_gray, size):
    return cv2.equalizeHist(cv2.resize(face_gray, size))


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
    """把某人改成 master / guest，立即生效（不用重新訓練：LBPH 只認編號，身分只存在 id_map.txt）。
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
    return sorted(p for p in cfg.face_dir.glob(f'{glob.escape(name)}_*')
                  if p.suffix.lower() in PHOTO_EXTS and p.stem.split('_')[0] == name)


def delete_member(cfg: Config, recognizer, name: str) -> int:
    """刪除某人：照片、名單、模型裡的特徵全部移除，無法復原。回傳刪掉的照片張數。

    順序：
      1. 先把他從名單拿掉 → 辨識執行緒下一幀就把他當陌生人（還在用舊模型也一樣）
      2. 刪照片
      3. 用剩下的照片重新訓練，face.yml 裡不再有他的特徵
      4. 新模型與新名單一起換上（編號會重排，所以兩個要同時換）
    至少要保留一位成員：全部刪光後 face.yml 會不存在，門鎖無法啟動。"""
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
            model = new_recognizer()
            model.read(str(cfg.model_file))
            recognizer.swap(model, read_map(cfg.map_file))
        log.info('已刪除成員 %s（%d 張照片），模型已重新訓練', name, len(files))
        return len(files)


@dataclass
class FaceResult:
    box: Tuple[int, int, int, int]      # x, y, w, h
    label: int
    distance: float
    person: str                          # id_map 裡的名字；不在名單上是 'Unknown'
    kind: str                            # master / guest / Stranger
    known: bool                          # 距離低於門檻，且身分是 master 或 guest

    @property
    def is_master(self) -> bool:
        return self.known and self.kind == 'master'

    @property
    def is_guest(self) -> bool:
        return self.known and self.kind == 'guest'


class FaceRecognizer:
    """把「找臉 + 認人」包成一個介面：identify(gray) → [FaceResult]。測試時可以換成假的。"""

    def __init__(self, cfg: Config, model, mapping, detector=None):
        self.cfg = cfg
        self.model = model
        self.mapping = mapping
        self.detector = detector or load_detector(cfg)
        self._lock = threading.Lock()

    def swap(self, model, mapping) -> None:
        """同時換上新模型與新名單（重新訓練後編號會重排，分開換會有一幀認錯人）。"""
        with self._lock:
            self.model, self.mapping = model, mapping

    @classmethod
    def load(cls, cfg: Config) -> 'FaceRecognizer':
        if not cfg.model_file.exists() or not cfg.map_file.exists():
            raise RuntimeError('缺少 face.yml 或 id_map.txt，請先 capture 再 train')
        model = new_recognizer()
        model.read(str(cfg.model_file))
        return cls(cfg, model, read_map(cfg.map_file))

    def detect(self, gray):
        return self.detector.detectMultiScale(gray, 1.2, 5, minSize=self.cfg.min_face)

    def identify(self, gray) -> List[FaceResult]:
        results = []
        with self._lock:
            model, mapping = self.model, self.mapping
        for x, y, w, h in self.detect(gray):
            label, distance = model.predict(normalize(gray[y:y + h, x:x + w], self.cfg.face_size))
            person, kind = mapping.get(label, ('Unknown', 'Stranger'))
            known = distance < self.cfg.threshold and kind in ('master', 'guest')
            results.append(FaceResult((int(x), int(y), int(w), int(h)), int(label), float(distance), person, kind, known))
        return results


# ==========================================
# 採集與訓練
# ==========================================
def capture(cfg: Config, user_id: str, count: int = None) -> int:
    """拍 count 張人臉存到 faces/；有螢幕時顯示預覽與進度條。回傳實際拍到的張數。"""
    from .camera import Camera, FrameReader
    from .display import draw_bar, draw_label, has_display

    count = count or cfg.capture_count
    if not re.fullmatch(r'[A-Za-z0-9-]+', user_id or ''):
        raise ValueError('ID 只能用英文、數字、減號（不可含底線或空格）')
    detector = load_detector(cfg)
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
            faces = detector.detectMultiScale(gray, 1.2, 5, minSize=cfg.min_face)
            if len(faces) == 1 and time.monotonic() - last_save >= 0.2:
                x, y, w, h = faces[0]
                if cv2.imwrite(str(cfg.face_dir / f'{user_id}_{stamp}-{saved}.jpg'), gray[y:y + h, x:x + w]):
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
    log.info('採集完成：%d 張。接著執行 train 重新訓練', saved)
    return saved


def train(cfg: Config) -> Dict[str, int]:
    """用 faces/ 裡所有照片訓練 LBPH，寫出 face.yml 與 id_map.txt。回傳每個人的照片張數。"""
    if not cfg.face_dir.exists():
        raise RuntimeError('沒有 faces 資料夾，請先採集')
    old_category = {name: cat for name, cat in read_map(cfg.map_file).values()}
    samples, ids, labels, per_person = [], [], {}, {}
    for path in sorted(cfg.face_dir.iterdir()):
        if path.suffix.lower() not in PHOTO_EXTS:
            continue
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            continue
        name = path.stem.split('_')[0]
        # faces/ 裡已是裁切好的人臉，不再重新偵測
        samples.append(normalize(img, cfg.face_size))
        ids.append(labels.setdefault(name, len(labels)))
        per_person[name] = per_person.get(name, 0) + 1
    if not samples:
        raise RuntimeError('faces 資料夾沒有照片，請先採集')
    model = new_recognizer()
    model.train(samples, np.asarray(ids, dtype=np.int32))
    model.write(str(cfg.model_file))
    # 保留手動改過的 guest 設定，新的人預設 master
    cfg.map_file.write_text(''.join(f'{num}:{name}:{old_category.get(name, "master")}\n'
                                    for name, num in labels.items()), encoding='utf-8')
    log.info('訓練完成：共 %d 張 → %s', len(samples), '、'.join(f'{n} {c} 張' for n, c in per_person.items()))
    return per_person
