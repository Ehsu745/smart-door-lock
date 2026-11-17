# 智慧門鎖 v2（模組化版）

人臉 + 手勢雙重驗證門鎖。Raspberry Pi 5 + Camera Module 3 + Arduino UNO + 繼電器。

指令跟舊版**完全一樣**（`capture` / `train` / `run` / `run --sim` / `qr` / `test-*`），已存的人臉照片**不用重拍**。

**v2.6 人臉辨識改用 YuNet + SFace**（OpenCV 內建的深度學習模型，取代 Haar + LBPH）。第一次啟動會自動下載兩個模型到 `models/`（約 39 MB），並用 `faces/` 既有照片建立 `face_feats.npz`，不用手動 train。距離門檻預設 50（越小越像），舊設定 `FACE_THRESHOLD=70` 要拿掉。

---

## 從舊版升級（Pi 上）

在 Mac 把整個資料夾傳過去（`faces/`、`face_feats.npz`、`id_map.txt`、`models/` 不會被動到；舊的 `face.yml` 已不使用，留著無妨）：

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
- **例外：變更主人 / 訪客、開鎖手勢每次都要重新輸入 PIN**，已登入也不算數；改完下一幀就生效，重新 train 也會保留。
- 成員頁右上 ⓘ 是主人 / 訪客的差別；新增成員的步驟收在清單下方「如何新增成員」。
- **刪除成員**：成員頁點進某人 →「刪除這位成員」→ 警告「此操作無法復原」→ 輸入 PIN。會永久刪除他的照片並自動重新登錄特徵，不用重新啟動；唯一的成員不能刪。
- PIN 來源：`pin.json`（網頁或 `python app.py set-pin` 設定，PBKDF2 雜湊、權限 600）優先，其次是 `smartlock.env` 的 `CONTROL_PIN`。
- 沒有 PIN 的手機按開鎖只會跳出鍵盤，不算 PIN 錯誤，不會觸發鎖定。
- 預覽版面不用接 Pi：直接雙擊 `smartlock/web/templates/index.html`，會自動進入示範模式（示範 PIN 1234）。

## 回報誤認（這不是我）

- 每次主人人臉通過都會記一筆（人臉小圖、距離、結果）。手機「紀錄」頁的開鎖 / 手勢逾時旁邊有「**這不是 某某？**」（**每次都要 PIN**）。
- 回報後立即生效，不用重新啟動：
  - **冒充者比對**：這張臉存進 `impostors/`。之後只要門口的臉比任何成員照片都更像某張冒充者照片，一律當陌生人。
  - **收緊門檻**：那個人的門檻收到「回報過最像的冒充者距離 − 3」，最低 35（`thresholds.json`）；只會變嚴，不會變鬆。
- 新門檻會擋掉本人超過 20% 的開鎖紀錄，或那張臉跟本人幾乎一樣時，會跳出警告。
- 回報錯了按「取消回報」（拿掉那筆特徵、重算門檻）；重新拍照 train 之後可在成員頁「恢復預設判定標準」。兩者都要 PIN。
- **成員自己的照片永遠不會被自動加入或更動**。原理與限制見 `docs/05_資料污染與判定標準.md`。
- 調整：`FACE_REPORT_MARGIN`（預設 3）、`FACE_REPORT_FLOOR`（預設 35）。這兩個數字還沒用門口實測資料驗證。

## 開鎖手勢（每個主人綁定一個）

- 每個主人可以綁定自己的開鎖手勢（MediaPipe 內建 7 種：讚、YA、倒讚、張開手掌、握拳、食指朝上、I Love You），**綁定後只有那一個能開門**；沒綁定的人沿用 `ALLOWED_GESTURES`（預設讚或 YA），升級後不會有人突然開不了門。
- 設定：手機「成員 → 點人 → 開鎖手勢」→ **先輸入 PIN** → 滾輪選 → 離開這頁就生效；或在 Pi 上 `python app.py set-gesture 名字 手勢`（`none` 取消，不加參數列出全部）。存在 `gestures.json`（權限 600），被改動時執行中的門鎖會自動重新讀取。
- **手勢是第二道密碼，不會洩漏**：門口螢幕只寫「Show your gesture」、不顯示目前看到的手勢；`/api/status`（不需登入）不給目前手勢，開鎖那一幀也不給；事件寫「綁定手勢」；手部骨架不用顏色表示對錯。成員詳情預設遮住（••••），輸入 PIN 進過手勢頁才顯示，離開詳情立即遮回去。
- **防止輪流試**：比錯的手勢**累計**（不是連續）達 `GESTURE_WRONG_FRAMES`（預設 15 幀，約 1 秒）就失敗並冷卻 3 秒，發 `gesture_wrong` 事件。這筆會出現在紀錄的「異常」，旁邊可以回報「這不是 某某？」（人臉過了但手勢錯，最可能是冒充者）。
- 刪除成員時會一起刪掉他的綁定。

## LINE 通知

1. 在 `smartlock.env` 加上 `LINE_CHANNEL_TOKEN=`（Developers Console → Messaging API）和 `LINE_BASIC_ID=@你的帳號`
2. `python app.py test-line`：確認 token 有效、看剩餘額度，再選擇是否送一則測試訊息
3. 手機「設定」分頁 →「加入通知好友」掃 QR，重新啟動門鎖後就會收到通知

| 通知 | 什麼時候發 |
|---|---|
| 訪客在門口 | guest 被認出時；同一位訪客 5 分鐘內只發一次（`LINE_COOLDOWN`） |
| 陌生人錄影 | 哨兵開始錄影時；兩次最少間隔 5 分鐘（`SENTINEL_COOLDOWN`） |
| 開鎖 / 上鎖失敗 | Arduino 沒回應或指令失敗；5 分鐘內只發一次 |

- 沒設 `LINE_USER_ID` 就**廣播給所有好友，每位好友各算一則**（輕用量方案每月 200 則）；有設就只 push 給那個人。
- 額度剩 20 則以下只發「訪客」和「開鎖失敗」；用完就暫停。通知只有文字，**不放開鎖連結**。
- 在背景執行緒發送，LINE 連不上不影響門鎖；失敗原因會顯示在手機「設定」的 LINE 區塊。

## 對講機（聽門口、預錄回覆）

硬體：Waveshare「USB TO AUDIO」免驅動音效卡（內建 2 顆麥克風，Pi 上顯示為「USB PnP Audio Device」）＋兩顆喇叭。
喇叭接在卡上標著 `VORN / VORP / VOLP / VOLN` 的 4-pin 插座，成套的喇叭是一個插頭直接插上，不用自己接線。
**左聲道（`VOLP/VOLN`）接門外喇叭、右聲道（`VORP/VORN`）接室內**（`test-audio` 先響的那顆是左聲道）；自己接線的話，BTL 輸出的 N 不是地線，不能接 GND、兩聲道負端不能並在一起。

1. 插上音效卡，`python app.py test-audio`：自動找卡 → 喇叭音量設 100% → 錄 5 秒 → 左、右聲道各播一次 → 印音量與 `vcgencmd get_throttled`（不是 `0x0` = 欠壓）
   - 喇叭完全沒聲音：這張卡在 Linux 上的音量刻度不對（`dmesg` 會有「cval->res is probably wrong」），預設 62% 實際幾乎是靜音；程式啟動時會自動設成 100%（`SPEAKER_VOLUME` 可改），手動測試前用 `amixer -c 卡號 sset Speaker 100%`
   - 錄音太小：`alsamixer` → F6 選 USB 卡 → F4 調麥克風增益（這張卡預設已經是最大），再靠近一點講話
   - 找不到卡或「Device or resource busy」：`arecord -l` 看卡號，在 `smartlock.env` 設 `AUDIO_DEVICE=plughw:卡號,0`（被 PipeWire 占用時改 `AUDIO_DEVICE=default`）
2. 預錄語音：在 Mac 上 `python tools/make_sounds.py` 產生三段預設語音（macOS 中文語音「美佳」），再 `rsync -av sounds/ pi:door_pi_gesture/sounds/`。
   **檔名就是按鈕上的字**，開頭的編號用來排序（`01 請稍候，馬上來.wav`）；格式 16-bit PCM WAV、最長 30 秒；想用自己的聲音就錄好用同名檔蓋掉，不用重新啟動
3. 啟動門鎖後，手機門鎖頁的影像右下角會出現「聆聽」「回覆」（沒有音效卡就不顯示）

- **聆聽**：有人在聽才開麥克風，最後一支手機停止就關掉；同一支手機只留一條、最多 4 條、10 分鐘換新（跟影像串流同一套規則）；手機落後超過 1 秒會丟掉舊的聲音，延遲維持在約 0.3 秒。離開門鎖頁、切到背景就停止
- **回覆**：從門外喇叭（左聲道，`INTERCOM_CHANNEL` 可改）播放，同一時間只播一段
- 兩者都要登入（PIN）；時間軸會記「手機開始聆聽門口」（同一支手機 15 分鐘內只記一次）和「門口播放：…」
- iPhone 靜音開關打開時，Safari 16.4 以上仍會出聲（`navigator.audioSession`）；更舊的版本要關掉靜音開關
- 只用 HTTP 就能做；手機對門口講話要用麥克風，瀏覽器規定要 HTTPS，之後再做
- Mac 開發：`INTERCOM=sim python app.py run --sim`（麥克風改成每 2 秒一聲嗶，回覆用 afplay 播）

## 程式結構

```
app.py                 入口（python app.py <指令>）
smartlock/
  config.py            所有設定（環境變數）集中一處
  events.py            事件匯流排：開鎖、訪客、失敗… 都發事件，錄影、紀錄、LINE 通知各自訂閱
  sentinel.py          哨兵：陌生人逗留自動錄影（預錄緩衝、背景寫檔）
  storage.py           SQLite 紀錄 + SD 卡容量管理 + 成員最近出現時間
  security.py          PIN 雜湊儲存、smartlock.env 載入
  line.py              LINE 額度查詢 + 通知（訪客、陌生人錄影、開鎖失敗；背景發送）
  door.py              狀態機（人臉 → 手勢 → 開鎖），不碰硬體，可單獨測試
  face.py              人臉偵測（YuNet）、辨識（SFace）、採集、登錄、個人門檻與冒充者比對
  learning.py          回報誤認：嘗試紀錄、收緊門檻、冒充者、取消回報
  bindings.py          每個主人綁定的開鎖手勢（gestures.json，檔案改動自動重新讀取）
  intercom.py          對講機：手機聽門口（arecord → raw PCM 串流）、播放預錄語音（aplay，分聲道）
  gesture.py           MediaPipe 手勢（含備援規則）
  arduino.py           序列埠協定（+ 模擬 Arduino）
  camera.py            rpicam / Picamera2 / USB 相機
  display.py           門口視窗畫面
  qr.py                控制頁 QR code
  web/                 Flask API + 單頁 App（templates/index.html、static/app.js、app.css）
  cli.py               指令列與選單
tests/                 183 個自動測試，不需要相機或 Arduino
deploy/                systemd 服務與安裝腳本
arduino/door_lock/     學弟的 Arduino sketch
```

## 測試涵蓋

- **狀態機**（假時鐘逐幀模擬）：master 人臉+手勢開鎖、比錯手勢累計失敗與重試、沒比手勢逾時、低信心手勢、只比手勢沒臉、手勢中離開、手短暫擋臉、停留不重複開鎖、離開 3 秒後重新偵測、只用人臉模式、guest 只通知不開鎖、多人入鏡、陌生人、自動模式關閉、Arduino 失敗。
- **Arduino 協定**：用虛擬序列埠模擬學弟的 sketch（有回覆、不回覆、看不懂指令）。
- **網頁**：PIN 錯誤/正確、連續錯誤鎖定、上鎖暫停自動、無效指令；紀錄頁需登入、回放、下載、刪除。
- **哨兵**：短暫出現不錄、超過 5 秒錄影且含預錄、master 出現取消、偵測短暫中斷容忍、遠處臉忽略、長時間分段、通知冷卻、迴圈變慢時影片仍是實際長度、SD 空間不足不錄。
- **容量管理**：依容量 / 天數 / SD 剩餘空間自動刪最舊的、中斷的錄影標成不完整、路徑不能跑出 records 資料夾。
- **PIN 與登入**：雜湊不存明碼、檔案權限 600、檔案優先於 CONTROL_PIN、set-pin 後執行中系統自動套用、改 PIN 後其他手機失效、沒附 PIN 不算錯誤。
- **新 API**：成員與頭像、設定、時間軸附錄影資訊（已刪除的錄影會標示）、LINE 額度快取與 token 錯誤。
- **回報誤認**：冒充者只擋長得像的那個人、本人照常、個人門檻只會變嚴、LBPH 舊門檻檔被忽略、嘗試紀錄與結果、人臉圖過期、端對端回報 → 再來開不了門 → 重啟仍有效 → 取消回報恢復、每次都要 PIN、門檻下限與擋到本人的警告、恢復預設、刪除成員一起清掉。
- **開鎖手勢**：只有綁定的手勢能開、沒綁定沿用預設、7 種都能綁、輪流試會被抓、比 YA 時短暫被認成食指朝上不算錯、低信心不算錯、狀態 / 事件 / 開鎖那一幀都不洩漏、檔案熱更新與壞檔、網頁 API 每次要 PIN、刪除成員一起刪、set-gesture 指令、手勢比錯記成可回報的嘗試。
- **對講機**：找 USB 音效卡、語音檔標籤與排序、立體聲轉單聲道、只播左 / 右聲道、多人共用一個麥克風、沒人聽就關麥克風、慢的手機丟最舊的資料、重連不重複記事件、同時只播一段、檔名不能跑出 sounds/、網頁 API 要登入 / PIN、串流格式。
- **其他**：設定讀取（含 smartlock.env）、手勢規則、QR code 可解碼、登錄保留 guest 分類、舊資料自動建立特徵。
