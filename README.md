# 智慧門鎖 v2（模組化版）

人臉 + 手勢雙重驗證門鎖。Raspberry Pi 5 + Camera Module 3 + Arduino UNO + 繼電器。

指令跟舊版**完全一樣**（`capture` / `train` / `run` / `run --sim` / `qr` / `test-*`），已存的人臉與模型**不用重新訓練**。

---

## 從舊版升級（Pi 上）

在 Mac 把整個資料夾傳過去（`faces/`、`face.yml`、`id_map.txt`、`models/` 不會被動到）：

```bash
scp -r smartlock app.py tests deploy requirements*.txt .gitignore \
    pi@192.168.0.50:~/door_pi_gesture/
```

在 Pi 上確認：

```bash
cd ~/door_pi_gesture && source .venv/bin/activate
uv pip install pytest          # 只有第一次需要
python -m pytest -q            # 應該全部 passed
python app.py run --sim        # 跟以前一樣啟動
```

舊的 `door_lock/` 資料夾可以留著，新版的 Arduino 程式放在 `arduino/door_lock/`（內容相同）。

---

## 開機自動啟動（systemd）

```bash
cp deploy/smartlock.env.example smartlock.env
nano smartlock.env                       # 改 CONTROL_PIN；接上 Arduino 後把 SIM_ARDUINO 改 0
bash deploy/install_service.sh           # 背景執行（不開視窗，用手機控制頁看畫面）
# 或
bash deploy/install_service.sh --desktop # 登入桌面後在 Pi 螢幕顯示視窗
```

| 動作 | 背景模式 | 桌面模式 |
|---|---|---|
| 看即時紀錄 | `journalctl -u smartlock -f` | `journalctl --user -u smartlock -f` |
| 停止 | `sudo systemctl stop smartlock` | `systemctl --user stop smartlock` |
| 重新啟動（改設定後） | `sudo systemctl restart smartlock` | `systemctl --user restart smartlock` |
| 移除服務 | `bash deploy/install_service.sh --remove` | 同左 |

- 停止服務時程式會先送 **LOCK** 再結束；當機會在 5 秒後自動重啟。
- 服務在跑的時候相機被佔用，要 `capture` / `test-camera` 前先 stop。

---

## 程式結構

```
app.py                 入口（python app.py <指令>）
smartlock/
  config.py            所有設定（環境變數）集中一處
  events.py            事件匯流排：開鎖、訪客、失敗… 都發事件，之後錄影、LINE 通知直接訂閱
  door.py              狀態機（人臉 → 手勢 → 開鎖），不碰硬體，可單獨測試
  face.py              人臉偵測、辨識、採集、訓練
  gesture.py           MediaPipe 手勢（含備援規則）
  arduino.py           序列埠協定（+ 模擬 Arduino）
  camera.py            rpicam / Picamera2 / USB 相機
  display.py           門口視窗畫面
  qr.py                控制頁 QR code
  web/                 Flask 控制頁（server.py + templates/ + static/）
  cli.py               指令列與選單
tests/                 49 個自動測試，不需要相機或 Arduino
deploy/                systemd 服務與安裝腳本
arduino/door_lock/     學弟的 Arduino sketch
```

## 測試涵蓋

- **狀態機**（假時鐘逐幀模擬）：master 人臉+手勢開鎖、錯誤手勢逾時與重試、低信心手勢、只比手勢沒臉、手勢中離開、手短暫擋臉、停留不重複開鎖、離開 3 秒後重新偵測、只用人臉模式、guest 只通知不開鎖、多人入鏡、陌生人、自動模式關閉、Arduino 失敗。
- **Arduino 協定**：用虛擬序列埠模擬學弟的 sketch（有回覆、不回覆、看不懂指令）。
- **網頁**：PIN 錯誤/正確、上鎖暫停自動、無效指令。
- **其他**：設定讀取、手勢規則、QR code 可解碼、訓練保留 guest 分類。
