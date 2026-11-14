"""SFace vs ArcFace（InsightFace buffalo_s / buffalo_l）辨識效能比較。

公平性：三個模型都用同一個 YuNet 偵測、同一組 5 點五官對齊，只換特徵模型。
每個模型跑兩種輸入：color（原圖）、gray（轉灰階再轉回 3 通道 = 門鎖目前的做法）。

準備（Mac 上；InsightFace 權重限非商業研究用途）：
  uv pip install onnxruntime
  mkdir -p models/insightface && cd models/insightface
  for m in buffalo_s buffalo_l; do
    curl -L -o $m.zip https://github.com/deepinsight/insightface/releases/download/v0.7/$m.zip && unzip -o $m.zip -d $m
  done
  curl -L -o ~/lfw-funneled.tgz https://ndownloader.figshare.com/files/5976015 && tar xzf ~/lfw-funneled.tgz -C ~

執行（在專案根目錄；需要 models/ 的 YuNet、SFace，faces/ 的登錄照片）：
  python tools/bench_face_models.py speed
  python tools/bench_face_models.py extract ~/lfw_funneled     # 約 1 萬張，Mac 上幾十分鐘
  python tools/bench_face_models.py report                     # 寫出 bench_out/report.json

輸出在 bench_out/（已列入 .gitignore；含人臉特徵，不要 commit）。
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path.cwd()
MODELS = ROOT / 'models'
YUNET = MODELS / 'face_detection_yunet_2023mar.onnx'
SFACE = MODELS / 'face_recognition_sface_2021dec.onnx'
ARC = {'arc_mbf': MODELS / 'insightface/buffalo_s/w600k_mbf.onnx',
       'arc_r50': MODELS / 'insightface/buffalo_l/w600k_r50.onnx'}
FACES = ROOT / 'faces'
OUT = ROOT / 'bench_out'
MAX_PER_PERSON = 6
ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
KEYS = [f'{m}_{c}' for m in ('sface', 'arc_mbf', 'arc_r50') for c in ('color', 'gray')]


class Models:
    def __init__(self, threads=2):
        missing = [p for p in (YUNET, SFACE, *ARC.values()) if not p.exists()]
        if missing:
            raise SystemExit('缺少模型檔（見檔頭的準備步驟）：\n  ' + '\n  '.join(map(str, missing)))
        self.det = cv2.FaceDetectorYN.create(str(YUNET), '', (320, 320), 0.6, 0.3, 5000)
        self.sface = cv2.FaceRecognizerSF.create(str(SFACE), '')
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.arc = {k: ort.InferenceSession(str(p), so, providers=['CPUExecutionProvider']) for k, p in ARC.items()}

    def detect(self, img):
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        if faces is None or len(faces) == 0:
            return None
        cx, cy = w / 2, h / 2          # LFW 每張以目標人物為中心：取最靠近中心的臉
        return min(faces, key=lambda f: (f[0] + f[2] / 2 - cx) ** 2 + (f[1] + f[3] / 2 - cy) ** 2)

    def f_sface(self, img, row):
        v = self.sface.feature(self.sface.alignCrop(img, row)).flatten()
        return v / np.linalg.norm(v)

    def f_arc(self, key, img, row):
        lm = row[4:14].reshape(5, 2).astype(np.float32)
        M, _ = cv2.estimateAffinePartial2D(lm, ARCFACE_DST, method=cv2.LMEDS)
        crop = cv2.warpAffine(img, M, (112, 112), borderValue=0)
        blob = cv2.dnn.blobFromImage(crop, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        sess = self.arc[key]
        v = sess.run(None, {sess.get_inputs()[0].name: blob})[0].flatten()
        return v / np.linalg.norm(v)

    def all_feats(self, img):
        out = {}
        for mode in ('color', 'gray'):
            src = img if mode == 'color' else cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            row = self.detect(src)
            if row is None:
                continue
            out[f'sface_{mode}'] = self.f_sface(src, row)
            for k in ARC:
                out[f'{k}_{mode}'] = self.f_arc(k, src, row)
        return out


def lfw_list(root):
    people = sorted(p for p in Path(root).expanduser().iterdir() if p.is_dir())
    return [(pid, f) for pid, p in enumerate(people) for f in sorted(p.glob('*.jpg'))[:MAX_PER_PERSON]]


def extract(lfw_dir):
    OUT.mkdir(exist_ok=True)
    m = Models()
    items = lfw_list(lfw_dir)
    feats = {k: [] for k in KEYS}
    ok = {k: [] for k in KEYS}
    t0 = time.time()
    for i, (pid, f) in enumerate(items):
        img = cv2.imread(str(f))
        got = m.all_feats(img) if img is not None else {}
        for k in KEYS:
            ok[k].append(k in got)
            feats[k].append(got.get(k, np.zeros(512 if 'arc' in k else 128, np.float32)))
        if i % 500 == 0:
            print(f'{i}/{len(items)}  {time.time() - t0:.0f}s', flush=True)
    np.savez_compressed(OUT / 'feats_lfw.npz', pid=np.array([p for p, _ in items]),
                        **{k: np.array(v, np.float32) for k, v in feats.items()},
                        **{f'ok_{k}': np.array(v) for k, v in ok.items()})
    # 登錄照片（灰階 162×162 緊裁切，跟門鎖相同）；目前只有一位成員，取 id_map 第一位
    own = {k: [] for k in KEYS}
    for f in sorted(FACES.glob('*.jpg')):
        img = cv2.cvtColor(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2BGR)
        got = m.all_feats(img)
        for k in KEYS:
            if k in got:
                own[k].append(got[k])
    np.savez_compressed(OUT / 'feats_own.npz', **{k: np.array(v, np.float32) for k, v in own.items()})
    print('done', len(items), 'images', {k: len(v) for k, v in own.items()})


def speed(n=200):
    m = Models(threads=1)
    img = cv2.imread(str(next(FACES.glob('*.jpg'))))
    row = m.detect(img)
    fns = {'sface': lambda: m.f_sface(img, row), 'arc_mbf': lambda: m.f_arc('arc_mbf', img, row),
           'arc_r50': lambda: m.f_arc('arc_r50', img, row)}
    res = {}
    for k, fn in fns.items():
        for _ in range(10):
            fn()
        t = time.perf_counter()
        for _ in range(n):
            fn()
        res[k] = round((time.perf_counter() - t) / n * 1000, 2)
    return res


def tar_at_far(gen, imp, fars=(1e-3, 1e-4, 1e-5)):
    imp_sorted = np.sort(imp)[::-1]
    out = {}
    for far in fars:
        k = max(int(far * len(imp)), 1)
        thr = float(imp_sorted[k - 1])
        out[str(far)] = {'tar': round(float((gen > thr).mean()), 4), 'cos_threshold': round(thr, 4),
                         'distance_threshold': round((1 - thr) * 100, 1)}
    return out


def eer(gen, imp):
    ths = np.linspace(-1, 1, 4001)
    frr = np.array([(gen < t).mean() for t in ths])
    far = np.array([(imp >= t).mean() for t in ths])
    i = int(np.argmin(np.abs(frr - far)))
    return round(float((frr[i] + far[i]) / 2), 5)


def report():
    d = np.load(OUT / 'feats_lfw.npz')
    own = np.load(OUT / 'feats_own.npz')
    pid = d['pid']
    out = {}
    for k in KEYS:
        ok = d[f'ok_{k}']
        F, P = d[k][ok], pid[ok]
        gen = []                                    # 本人配對：同一人所有組合
        for p in np.unique(P):
            idx = np.where(P == p)[0]
            if len(idx) >= 2:
                S = F[idx] @ F[idx].T
                gen.extend(S[np.triu_indices(len(idx), 1)])
        gen = np.array(gen)
        G = F[np.unique(P, return_index=True)[1]]   # 陌生人配對：每人第一張兩兩比較
        imp = (G @ G.T)[np.triu_indices(len(G), 1)]
        A = own[k]                                  # 門鎖情境：登錄照片當相簿，LFW 每人第一張當陌生人
        loo = [(np.delete(A, i, 0) @ A[i]).max() for i in range(len(A))] if len(A) > 1 else []
        stranger = (G @ A.T).max(1) if len(A) else np.array([np.nan])
        out[k] = dict(
            detected=f'{int(ok.sum())}/{len(ok)}', genuine_pairs=len(gen), impostor_pairs=len(imp),
            tar_at_far=tar_at_far(gen, imp), eer=eer(gen, imp),
            own_photos=len(A), own_worst_distance=round((1 - float(min(loo))) * 100, 1) if loo else None,
            strangers=len(stranger), closest_stranger_distance=round((1 - float(stranger.max())) * 100, 1),
        )
    out['speed_ms_1thread'] = speed()
    (OUT / 'report.json').write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    cmds = {'extract': lambda: extract(sys.argv[2]), 'report': report, 'speed': lambda: print(speed())}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        raise SystemExit(__doc__)
    cmds[sys.argv[1]]()
