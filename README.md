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

## 哨兵（陌生人自動錄影）

| 規則 | 預設 | 環境變數 |
|---|---|---|
| 陌生人連續出現多久開始錄 | 5 秒（期間認出 master 就取消） | `SENTINEL_TRIGGER` |
| 觸發前保留的畫面 | 5 秒 | `SENTINEL_PREROLL` |
| 陌生人離開多久停止 | 5 秒 | `SENTINEL_STOP` |
| 單段最長 | 60 秒，人還在就接著錄下一段 | `SENTINEL_MAX` |
| 臉寬下限（排除遠處路人） | 80 px | `SENTINEL_MIN_FACE` |
| 兩次通知最短間隔 | 5 分鐘 | `SENTINEL_COOLDOWN` |

- **格式**：MJPG `.avi`，10 fps，約 0.3 MB/秒（60 秒約 20 MB）。另存臉最大的一張截圖。
- **查看**：手機「紀錄」分頁的時間軸，錄影直接嵌在時間軸裡，可回放、2 倍速、下載、刪除。
- **權限**：有 PIN 時，看紀錄、成員、設定也要先輸入 PIN；PIN 連續錯 5 次鎖 60 秒。
- **SD 卡容量**：超過 7 天、總量超過 2 GB、或 SD 卡剩不到 1 GB 時，自動從最舊的刪起（啟動時、每段錄完、每小時檢查）。文字事件保留 90 天。
- 資料都在 `records/`（`smartlock.db` + `clips/`），已列入 `.gitignore`，不會推上 GitHub。

## 手機介面與 PIN

- 單頁 App，底部四個分頁（門鎖 / 成員 / 紀錄 / 設定），點或左右滑動切換；整頁不捲動，只有紀錄和設定的內容區可以捲。
- **按住鎖 1 秒才開門**，避免誤觸；「立即上鎖」和「自動解鎖」開關分開。
- 輸入一次 PIN 後，這支手機在本次啟動期間都不用再輸入；改 PIN 後所有手機都要重新輸入。
- **例外：變更主人 / 訪客每次都要重新輸入 PIN**，已登入也不算數；改完寫回 `id_map.txt`，下一幀就生效，重新 train 也會保留。
- **刪除成員**：成員頁點進某人 →「刪除這位成員」→ 警告「此操作無法復原」→ 輸入 PIN。會永久刪除他的照片並自動重新訓練，不用重新啟動；唯一的成員不能刪。
- PIN 來源：`pin.json`（網頁或 `python app.py set-pin` 設定，PBKDF2 雜湊、權限 600）優先，其次是 `smartlock.env` 的 `CONTROL_PIN`。
- 沒有 PIN 的手機按開鎖只會跳出鍵盤，不算 PIN 錯誤，不會觸發鎖定。
- 預覽版面不用接 Pi：直接雙擊 `smartlock/web/templates/index.html`，會自動進入示範模式（示範 PIN 1234）。

## 程式結構

```
app.py                 入口（python app.py <指令>）
smartlock/
  config.py            所有設定（環境變數）集中一處
  events.py            事件匯流排：開鎖、訪客、失敗… 都發事件，錄影、紀錄、之後的 LINE 通知各自訂閱
  sentinel.py          哨兵：陌生人逗留自動錄影（預錄緩衝、背景寫檔）
  storage.py           SQLite 紀錄 + SD 卡容量管理 + 成員最近出現時間
  security.py          PIN 雜湊儲存、smartlock.env 載入
  line.py              LINE 額度查詢（通知發送下一步加入）
  door.py              狀態機（人臉 → 手勢 → 開鎖），不碰硬體，可單獨測試
  face.py              人臉偵測、辨識、採集、訓練
  gesture.py           MediaPipe 手勢（含備援規則）
  arduino.py           序列埠協定（+ 模擬 Arduino）
  camera.py            rpicam / Picamera2 / USB 相機
  display.py           門口視窗畫面
  qr.py                控制頁 QR code
  web/                 Flask API + 單頁 App（templates/index.html、static/app.js、app.css）
  cli.py               指令列與選單
tests/                 108 個自動測試，不需要相機或 Arduino
deploy/                systemd 服務與安裝腳本
arduino/door_lock/     學弟的 Arduino sketch
```

## 測試涵蓋

- **狀態機**（假時鐘逐幀模擬）：master 人臉+手勢開鎖、錯誤手勢逾時與重試、低信心手勢、只比手勢沒臉、手勢中離開、手短暫擋臉、停留不重複開鎖、離開 3 秒後重新偵測、只用人臉模式、guest 只通知不開鎖、多人入鏡、陌生人、自動模式關閉、Arduino 失敗。
- **Arduino 協定**：用虛擬序列埠模擬學弟的 sketch（有回覆、不回覆、看不懂指令）。
- **網頁**：PIN 錯誤/正確、連續錯誤鎖定、上鎖暫停自動、無效指令；紀錄頁需登入、回放、下載、刪除。
- **哨兵**：短暫出現不錄、超過 5 秒錄影且含預錄、master 出現取消、偵測短暫中斷容忍、遠處臉忽略、長時間分段、通知冷卻、迴圈變慢時影片仍是實際長度、SD 空間不足不錄。
- **容量管理**：依容量 / 天數 / SD 剩餘空間自動刪最舊的、中斷的錄影標成不完整、路徑不能跑出 records 資料夾。
- **PIN 與登入**：雜湊不存明碼、檔案權限 600、檔案優先於 CONTROL_PIN、set-pin 後執行中系統自動套用、改 PIN 後其他手機失效、沒附 PIN 不算錯誤。
- **新 API**：成員與頭像、設定、時間軸附錄影資訊（已刪除的錄影會標示）、LINE 額度快取與 token 錯誤。
- **其他**：設定讀取（含 smartlock.env）、手勢規則、QR code 可解碼、訓練保留 guest 分類。
