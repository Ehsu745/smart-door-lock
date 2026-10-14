# 智慧門鎖雛形（Pi 5 + Pi 相機 + Arduino）

```
Pi 相機 ──排線──> Raspberry Pi 5（人臉辨識 + 網頁）──USB 線──> Arduino UNO ──D7──> 繼電器 ──> 門鎖電源
```

## 檔案
| 檔案 | 放哪裡 | 用途 |
|---|---|---|
| `app.py` | Pi，例如 `~/door_pi/` | 採集、訓練、辨識、網頁、送指令給 Arduino |
| `door_lock/door_lock.ino` | 用 Arduino IDE 上傳到 UNO | 收到 `UNLOCK` 讓繼電器動作 5 秒，收到 `LOCK` 立即上鎖 |
| `serial_diag.py` | Pi | Arduino 連不上時用來診斷 |
| `haarcascade_frontalface_default.xml` | 從舊專案複製，放在 `app.py` 同一層 | 人臉偵測模型 |

舊專案的 `faces/`、`face.yml`、`id_map.txt` 可以直接複製過來。**但一定要重新執行一次 `train`**，因為新版在訓練前會先把人臉統一成 200×200 並做直方圖均衡化，舊的 `face.yml` 不相容。

## 1. 安裝（Pi 上用系統 Python，不要用 venv）
```bash
sudo apt update
sudo apt install -y python3-opencv python3-picamera2 python3-flask python3-serial opencv-data
python3 -c "import cv2; print(cv2.__version__, hasattr(cv2, 'face'))"   # 必須印出 True
rpicam-hello --list-cameras                                              # 必須看到相機
```
- 如果 `hasattr(cv2, 'face')` 是 False，表示用 pip 裝過 `opencv-python`。先 `pip uninstall opencv-python`，改用 apt 版。
- Picamera2 是 apt 套件，venv 預設看不到它。這也是建議用系統 Python 的原因。

## 2. Arduino
1. 用 Arduino IDE 把 `door_lock/door_lock.ino` 上傳到 UNO。
2. 先單獨測試：打開 Serial Monitor，鮑率 9600，行尾選 Newline，輸入 `UNLOCK`。應該會聽到繼電器喀一聲，5 秒後再喀一聲。
3. **關掉 Serial Monitor**，再用 USB 線接到 Pi。

## 3. 使用
```bash
cd ~/door_pi
python3 app.py capture yang    # 採集 30 張。ID 只能用英文、數字、減號
python3 app.py train           # 每次新增或刪除人臉後都要重新訓練
python3 app.py test-arduino    # 只測 Arduino：解鎖 → 6 秒後上鎖
python3 app.py run             # 正式啟動
python3 app.py                 # 或使用互動選單
```
- 啟動後，網頁控制台的網址是 `http://localhost:5050`。同一個網路裡的其他裝置用 `http://<Pi 的 IP>:5050`。
- 網頁上可以看到即時影像、辨識結果、Arduino 連線狀態和事件紀錄，也有「解鎖／上鎖並暫停自動／恢復自動」三個按鈕。
- 透過 SSH 執行也可以：`capture` 會自動改成不開預覽視窗；`run` 本來就只用網頁顯示。
- 辨識門檻可以用環境變數調整，例如 `FACE_THRESHOLD=60 python3 app.py run`。數值越低越嚴格。執行時網頁會顯示每張臉的距離值，可以拿本人和陌生人實測後再決定。
- Arduino 埠預設會自動尋找，也可以手動指定：`ARDUINO_PORT=/dev/ttyACM0 python3 app.py run`。

## 行為
- 畫面中只有一個人，而且是 master 或 guest，連續 8 幀辨識通過，就會送出 `UNLOCK`。Arduino 開 5 秒後自動上鎖。
- 同一個人一直站在鏡頭前不會重複解鎖。所有人離開鏡頭至少 3 秒後，才會再次允許自動解鎖。
- 有多人入鏡或出現陌生人時，不會自動解鎖。
- Arduino 的 USB 被拔掉後，網頁會顯示斷線；重新插回去會自動重連，不用重開程式。
- 按 Ctrl+C 結束時，程式會先送出 `LOCK` 再退出。
- 想把某個人改成訪客，就編輯 `id_map.txt`，把 `master` 改成 `guest`。之後重新訓練時，這個設定會保留。

## 接線（請先確認門鎖規格）
- 繼電器板：VCC 接 UNO 5V，GND 接 UNO GND，IN1 接 D7。程式預設是**低電位觸發**。
- 如果繼電器是高電位觸發，就把 `.ino` 裡 `UNLOCK_LEVEL` 和 `LOCK_LEVEL` 的值對調。
- 門鎖使用**獨立的 12V 電源**，透過繼電器的 COM/NO 開關。鎖的兩端要反向並聯一顆 1N4007。
- 如果用的是**磁力鎖**（通電＝上鎖），改接 COM/NC，否則動作會整個相反。

## 疑難排解
| 症狀 | 處理 |
|---|---|
| `鏡頭無法開啟` 或 Picamera2 錯誤 | 執行 `rpicam-hello --list-cameras`；關掉其他占用相機的程式（例如舊版 app、rpicam-hello） |
| 網頁顯示 `找不到 Arduino` | 換一條可以傳資料的 USB 線；`ls /dev/ttyACM*` |
| 網頁顯示 `有開啟但 Arduino 沒回應` | 重新上傳 `door_lock.ino`；確認 Serial Monitor 已關閉 |
| 其他連線問題 | 執行 `python3 serial_diag.py` |
| `缺少 cv2.face` | 見第 1 節 |
| `找不到 haarcascade...xml` | 從舊專案把這個 xml 複製到 `app.py` 同一層 |
| 一直認不出本人 | 採集時多換幾個角度，光線和現場一致；調高 `FACE_THRESHOLD` |

## 限制（原型）
- 網頁沒有登入機制，同一個網路的任何人都能按解鎖。
- LBPH 沒有活體偵測，拿照片可能騙得過。
- Arduino 回覆 `UNLOCKED` 只代表繼電器腳位已經動作，系統沒有門磁感測器可以確認門真的開了。
