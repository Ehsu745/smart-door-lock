# 人臉 + 手勢雙重驗證版：環境與使用

## 開鎖流程
```
認出已登錄的人臉（連續 8 幀）
   ↓  畫面和網頁提示「請比出手勢 👍 或 ✌️（剩 N 秒）」
8 秒內比出合格手勢（連續 5 幀、信心值 ≥ 0.6）
   ↓
送 unlock 給 Arduino → 8 秒後自動上鎖
```
- 只比手勢、沒有人臉：不會開。
- 人臉通過但比錯手勢，或時間到了：取消，3 秒後可以重試。
- 比手勢時手擋到臉沒關係，人臉消失 3 秒內都還算數。
- 開鎖後要所有人離開鏡頭 3 秒，才會再次自動開鎖（和舊版相同）。

## 為什麼要換 Python 版本
MediaPipe 目前在 Linux ARM（Pi）上的最新版是 **0.10.18**，只支援 **Python 3.9–3.12**；Pi 系統的 Python 3.13 裝不了。
所以這一版要在獨立的 venv 裡執行。相機改用 `rpicam-vid` 讀取，**不需要 Picamera2，也不需要 libcamerify**。

## Pi 上建立環境（擇一）

**方法 A：uv（推薦，約 1 分鐘，不用編譯）**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # 裝完重開終端機
cd ~/door_pi_gesture
uv venv -p 3.11 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

**方法 B：沿用 Pi 上已有的 pyenv Python 3.9.18**
```bash
cd ~/door_pi_gesture
~/.pyenv/versions/3.9.18/bin/python -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
```

確認：
```bash
python -c "import mediapipe, cv2; print(mediapipe.__version__, hasattr(cv2,'face'))"   # 0.10.18 True
```
- 之後每次開新的終端機，都要先執行 `source .venv/bin/activate`。
- **不要**另外 `pip install opencv-python`，否則會蓋掉含 `cv2.face` 的版本。

## Mac 上建立環境與測試

**⚠️ 一定要用 Python 3.11 或 3.12。** Homebrew 的 `python3` 通常是 3.13 以上，用它建 venv 會出現 `mediapipe==0.10.18 has no wheels` 的錯誤。用 uv 指定版本最省事（Apple Silicon 和 Intel Mac 都可以）：

```bash
brew install uv                      # 或 curl -LsSf https://astral.sh/uv/install.sh | sh
cd door_pi_gesture
uv venv -p 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
python -c "import sys, mediapipe, cv2; print(sys.version[:4], mediapipe.__version__, hasattr(cv2,'face'))"
# 應該印出：3.12 0.10.18 True
```

**Mac 上的測試順序**（不需要 Arduino）：
```bash
python app.py test-gesture          # 1. 先測手勢：對鏡頭比讚、比 YA，看有沒有出現「← 合格」
python app.py capture me            # 2. 拍自己的臉（會跳出預覽視窗，按 q 可提早結束）
python app.py train                 # 3. 訓練
python app.py run --sim             # 4. 模擬執行，瀏覽器開 http://localhost:5050
```
第 4 步：先看鏡頭讓它認出你，網頁上會出現「請比出手勢 👍 或 ✌️（剩 N 秒）」，比出來之後，終端機會顯示 `[模擬 Arduino] 繼電器 ON → 開鎖 🔓`。按 `Ctrl` + `C` 結束。

**Mac 常見狀況：**
| 狀況 | 處理 |
|---|---|
| `鏡頭 0 無法開啟` | 「系統設定 → 隱私權與安全性 → 相機」打開你用的終端機 App（Terminal / iTerm / VS Code），**重開那個 App** 再試 |
| 畫面是 iPhone 的鏡頭 | 「接續互通相機」搶走了編號 0，改用 `CAMERA_INDEX=1 python app.py ...` |
| 手勢一直是「備援模式」 | 模型沒下載成功。手動下載：`mkdir -p models && curl -L -o models/gesture_recognizer.task <下面「手勢模型」段落的網址>` |
| `has no wheels with a matching Python ABI tag` | Python 版本太新，照上面用 `uv venv -p 3.12` 重建 |
| 想在 Mac 上接 Arduino 測試 | 直接插 USB，程式會自動找到 `/dev/cu.usbmodem*`，把 `--sim` 拿掉就好 |

Mac 測完要搬到 Pi 時，**只傳程式，不要傳 `.venv`、`models`、`faces`**：
```bash
rsync -avP --exclude .venv --exclude models --exclude faces --exclude face.yml --exclude id_map.txt \
  door_pi_gesture/ pi@192.168.0.50:~/door_pi_gesture/
```

## 手勢模型
第一次執行時，程式會自動下載 MediaPipe 官方手勢模型（約 8 MB），存到 `models/gesture_recognizer.task`。
如果下載失敗，程式會自動改用**備援模式**（MediaPipe Hands 加上關鍵點規則），照樣可以使用。也可以手動下載：
```bash
mkdir -p models && wget -O models/gesture_recognizer.task \
  https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task
```

## 指令
和原本的版本相同，另外多了 `test-gesture`：
```bash
python app.py test-gesture     # 先單獨測試手勢：每 0.5 秒印出偵測到的手勢和信心值
python app.py capture me       # 採集人臉（可以直接沿用舊版的 faces/、face.yml、id_map.txt）
python app.py train
python app.py run --sim        # 不接 Arduino，用文字模擬開鎖
python app.py run              # 正式啟動
```
在 venv 裡 `python` 就是 3.11（或 3.9），不用打 `python3`。

## 可以調整的設定（寫在指令前面）
| 設定 | 預設 | 說明 |
|---|---|---|
| `ALLOWED_GESTURES` | `Thumb_Up,Victory` | 哪些手勢可以開門。可選：`Thumb_Up` 👍、`Victory` ✌️、`Open_Palm` ✋、`Closed_Fist` ✊、`Pointing_Up` ☝️、`Thumb_Down` 👎、`ILoveYou` 🤟 |
| `GESTURE_SCORE` | `0.6` | 手勢信心值門檻（0–1），越高越嚴格 |
| `GESTURE_FRAMES` | `5` | 要連續幾幀都是合格手勢才算 |
| `GESTURE_WINDOW` | `8` | 人臉通過後，幾秒內要比出手勢 |
| `REQUIRE_GESTURE` | `1` | 設成 `0` 就關閉手勢，只用人臉開門（等於舊版） |
| `CAMERA` | `auto` | `rpicam`（Pi 相機）、`usb`（USB webcam）、`picamera2` |
| `CAMERA_INDEX` | `0` | USB / Mac 鏡頭的編號 |

範例：只接受比讚，信心值要 0.7 以上
```bash
ALLOWED_GESTURES=Thumb_Up GESTURE_SCORE=0.7 python app.py run
```

## 注意
- 這台是 NoIR 相機，白天畫面偏紫。MediaPipe 是用一般彩色影像訓練的，手勢偵測率可能會受一點影響，請用 `test-gesture` 實測。必要時可以改用 USB webcam（`CAMERA=usb`）。
- 等待手勢的期間才會執行 MediaPipe，其他時間只跑人臉辨識，所以平常的 CPU 負擔和舊版差不多。
- 網頁會顯示目前階段、倒數秒數和偵測到的手勢。
