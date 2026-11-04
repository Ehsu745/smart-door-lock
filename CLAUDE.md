# CLAUDE.md — 智慧門鎖交接文件

給接手的 Claude Code 讀的。先讀完這份，再讀 `README.md`（使用說明）和 `docs/`（規劃）。

---

## 1. 專案是什麼

逢甲通訊系學弟的畢業專題：**人臉 + 手勢雙重驗證的智慧門鎖**。使用者（學長）負責軟體與整合，學弟負責 Arduino 與硬體。

```
Pi Camera 3 NoIR ─▶ Raspberry Pi 5 (4GB) ──USB 序列埠──▶ Arduino UNO ─▶ 繼電器 ─▶ 電磁鎖
                         │
                         └─ Flask 網頁（手機控制、紀錄、設定）── 之後：LINE 通知
```

- 人臉：Haar 偵測 + LBPH 辨識（距離 < 70 算認得），`master` 連續 8 幀通過 → 進手勢階段
- 手勢：MediaPipe 0.10.18 Gesture Recognizer（模型下載失敗時改用 Hands + 規則），讚 / YA 連續 5 幀 → 開鎖
- `guest` 永遠不自動開鎖，只通知；陌生人逗留 ≥ 5 秒 → 哨兵錄影
- Arduino：送小寫 `unlock` / `lock` + `\n`，sketch 回 `Unlocked: ...` / `Locked`，8 秒後自動上鎖

## 2. 目前狀態（v2.4.0）

| 優化規劃項目 | 狀態 |
|---|---|
| 1. 模組化 + 狀態機測試 | ✅ |
| 2. systemd 背景服務 | ✅（`deploy/`） |
| 3. 哨兵錄影 + SQLite + 控制頁 | ✅ |
| 4. LINE 文字通知 | ⏳ **下一步**（額度查詢已完成，發送還沒做） |
| 5. 軟體移動偵測 + 效能量測 | ⬜ |

額外完成：手機單頁 App（門鎖 / 成員 / 紀錄 / 設定）、PIN 雜湊與網頁變更、網頁變更主人/訪客、網頁刪除成員（v2.4：警告無法復原 → PIN → 刪照片並重新訓練）、串流連線數修正（網站「當掉」問題）、依手機尺寸排版。

**還沒在實機驗證的**：v2.2.2 之後的手機版面、v2.3 的變更權限 —— 都只在模擬環境（Playwright + 假相機）測過。學弟的 Pi 上可能還在跑舊的單檔 `app.py`。

## 3. 常用指令

```bash
source .venv/bin/activate
python -m pytest -q                 # 108 個測試，不需要相機或 Arduino（Mac：104 passed, 4 skipped，序列埠 pty 測試只在 Linux 跑）
python app.py run --sim             # 完整系統，用模擬 Arduino
python app.py records | cleanup | set-pin | test-camera | test-gesture | test-arduino
# 只看版面（不用 Pi）：直接用瀏覽器開 smartlock/web/templates/index.html → 自動示範模式，PIN 1234
```

**改完程式一定要跑 pytest**；改前端要實際開瀏覽器看（Playwright 截圖，至少 375×548 和 390×664 兩種尺寸）。

## 4. 程式結構與關鍵設計

```
app.py                 入口（相容舊指令：python app.py run）
smartlock/
  config.py            所有設定，從環境變數讀（Config.from_env）
  security.py          PinStore（PBKDF2，pin.json 0600）、load_env_file（自動讀 smartlock.env）
  events.py            EventBus：狀態機只發事件，其他模組訂閱
  door.py              DoorSystem 狀態機；process_frame() 可注入 clock，測試用假時鐘逐幀跑
  face.py              偵測、辨識、capture、train、set_role（改 id_map.txt 並熱更新 mapping）
  gesture.py / arduino.py / camera.py / display.py / qr.py
  sentinel.py          哨兵：預錄緩衝、固定幀率取樣（迴圈慢時補幀）、背景寫檔 MJPG .avi
  storage.py           SQLite（records / events / presence）+ 容量管理（天數 / 總量 / SD 剩餘）
  line.py              LINE 額度查詢（10 分鐘快取）；之後在這裡加發送
  web/server.py        Flask API + PinGuard（5 次錯鎖 60 秒）+ StreamRegistry
  web/templates/index.html, web/static/app.js, app.css, qrcode.min.js（MIT，內嵌）
  cli.py               指令、run() 組裝所有元件
tests/                 conftest.py 有 Harness（假辨識器 / 假手勢 / 假 Arduino / 假時鐘）
deploy/                smartlock.service、install_service.sh、smartlock.env.example
docs/                  01 居家助理未來規劃（概念階段）、02 智慧門鎖優化規劃
```

**不要破壞的規則**
- **使用者既有資料不能要求重新訓練**：`faces/`、`face.yml`、`id_map.txt`（格式 `編號:名字:master|guest`）、`models/` 必須相容。`train` 會保留既有的 guest/master 設定。
- **重新允許自動開鎖的條件**：鏡頭裡沒有臉持續 3 秒。使用者明確說過不要改這個規則。
- **guest 永遠不自動開鎖**。
- **測試不能碰專案資料夾**：Harness 會把 `Config.base` 換成暫存目錄（Pi 上可能有真的 `pin.json`、`records/`）。
- 新功能優先用「訂閱事件」做，不要把邏輯塞進 `door.py`。
- 前端：整個 App `position: fixed`，**不要用 `100dvh`**（LINE 內建瀏覽器不支援，曾造成整頁捲動）；門鎖頁尺寸由 `fitDoor()` 依實際高度計算；每秒輪詢只改有變的欄位。
- 影像串流：同裝置只留一條、全部最多 4 條、10 分鐘換新、20 秒 socket timeout。瀏覽器同網域約 6 條連線上限，舊分頁曾讓網站看起來「當掉」。

**PIN 規則**
- 來源優先順序：`pin.json`（雜湊）> `smartlock.env` 的 `CONTROL_PIN` > 無
- 輸入一次後同手機免再輸入（session，改 PIN 後全部失效）；沒附 PIN 回 401 不算錯誤
- **變更主人/訪客每次都要 PIN，已登入也不算**（使用者明確要求）；錯誤鎖定仍保留
- **刪除成員**同樣每次都要 PIN，而且前端要先顯示「此操作無法復原」警告（使用者明確要求）；API 需帶 `confirm=<名字>`；不能刪最後一位成員。`face.delete_member` 先移出名單 → 刪照片 → 重新 train → `FaceRecognizer.swap()` 同時換模型和名單（編號會重排）

## 5. 下一步：LINE 通知（規劃已定）

- 帳號已建好：官方帳號 `@your_line_id`，輕用量方案（每月 200 則，**廣播按好友人數計算**）
- 使用者還沒拿到 token、還沒做 curl 測試。使用者的 Business ID 沒連動 LINE 帳號，所以 Console 看不到 Your user ID → 先用 **broadcast**；有 `LINE_USER_ID` 時改用 push
- 設定放 `smartlock.env`：`LINE_CHANNEL_TOKEN`、`LINE_BASIC_ID`、`LINE_USER_ID`（**token 絕不寫進程式或 git**）
- 做法：`LineNotifier` 訂閱事件
  - `stranger_detected`：只在 `data['notify']` 為 True 時發（哨兵已做 5 分鐘冷卻）
  - `guest_detected`、`door_failed`
  - 發送後呼叫 `LineClient.count_sent(n)`；額度剩 20 以下只發重要的（door_failed、guest）
  - 在背景執行緒發送，不能卡住辨識迴圈；失敗要記錄但不能讓系統掛掉
- **通知內容不放開鎖連結**；圖片需要公開 HTTPS 網址，先只發文字
- 測試：用假的 opener（參考 `tests/test_security.py` 的 FakeResp），不要真的打 LINE API

之後：軟體移動偵測（低解析度幀差，沒動靜時跳過辨識），改前改後用 `htop`、`vcgencmd measure_temp` 量 CPU 與溫度，放進報告。

## 6. 環境

- **Pi**：Raspberry Pi OS Trixie 64-bit，hostname `pi`、使用者 `pi`，專案在 `~/door_pi_gesture`，`.venv` 用 uv 建（Python 3.11）
- 網路：目前用 iPhone 熱點（Pi IP 曾是 `192.168.0.50`，會變）；學校 FCU-Auto 是 WPA2-Enterprise，放棄。之後可能改乙太網路 + Pi 開熱點（`nmcli device wifi hotspot`），還在確認場地
- 用 SSH 啟動要在 Pi 螢幕顯示視窗：前面加 `DISPLAY=:0`
- MediaPipe 0.10.18 沒有 Python 3.13 的 ARM wheel → Python 3.9–3.12
- 相機後端順序：rpicam-vid（YUV420 pipe）→ Picamera2 → USB（`CAMERA_INDEX`）
- **使用者的 Mac**：Python 3.11（`/opt/homebrew/bin/python3.11`）、uv 有裝（`/opt/homebrew/bin/uv`）；系統內建 `python3` 是 3.9.6，不要用它建 venv。建環境：`uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt`
- 部署到 Pi：複製 `smartlock/`、`app.py`、`tests/` 到 `~/door_pi_gesture`，資料檔不動；資料庫會自動升級

### Git / GitHub

- Repo：`~/GITHUB/smart-door-lock`（使用者所有 git 專案都放在 `~/GITHUB/`）→ GitHub `Ehsu745/smart-door-lock`，**目前是 private**
- `git log` 從最舊到最新完整保留：PC 測試版（10/14）→ Pi 單檔版 → 手勢單檔版（10/19、10/20）→ v2 模組化（11/03）→ v2.x（11/04）。舊版原始檔在 `~/FCU/CE_2026/AI_smartlock_playground/old_ver/`，不要動
- 遠端有 `backup/pre-rebuild` 分支（2026-11-04 重建歷史前的 main），確認沒問題後可刪
- Mac 上的開發 / 訓練資料夾：`~/FCU/CE_2026/AI_smartlock_playground/smartlock_project`（有 `.venv`、`faces/`、`face.yml`、`records/`），和 repo 分開；改完要同步進 repo 再 commit
- **待辦（使用者決定延後）**：公開 repo 前要清掉歷史裡的敏感資訊（README 有 Pi 帳號與 IP `pi@192.168.0.50`），需要改寫歷史 + force push，先問

## 7. 跟使用者合作的方式

- **一律用繁體中文回覆**；結論先講，精簡
- **CLI 優先**；使用者懂嵌入式與 Python，不用解釋基礎
- 給學弟看的文件要**看不懂程式的人也能照做**，學弟偏好 Word / PDF
- 只想更新單一檔案時，盡量讓改動集中（使用者曾要求「只改 app.py」）
- **主動提醒值得寫進履歷 / GitHub 的成果**（使用者在找系統驗證 / 通訊韌體工程師職缺）
- 要動到使用者明確定過的規則前，先問
- Commit 結尾加：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## 8. 已知問題

| 問題 | 狀態 / 對策 |
|---|---|
| 網站「常當掉、Pi 還在跑」 | v2.2.1 修掉（串流佔滿瀏覽器連線）。若再發生：Pi 上 `curl localhost:5050/api/health`，有回應就是網路問題（熱點休眠、IP 變、Wi-Fi 省電） |
| 主人站在門口不比手勢，每 11 秒記一筆「手勢逾時」 | 前端已合併顯示（×N）；後端照記 |
| 同一人在 master / Unknown 間跳動 | 在門口實際光線重拍，距離穩定 < 50 再上線 |
| NoIR 畫面偏紫 | 不影響辨識；手勢不夠準改 USB 相機 |
| SD 卡曾裂過、重灌 | 用 `sudo poweroff` 關機、5V/5A 電源，長期考慮 USB SSD |
| Wi-Fi 省電可能讓 Pi 斷線 | `nmcli connection modify <連線> 802-11-wireless.powersave 2` |

## 9. 隱私與機密

- 不要 commit：`faces/`、`face.yml`、`id_map.txt`、`models/`、`records/`、`pin.json`、`smartlock.env`（都在 `.gitignore`）；`.gitignore` 另外擋掉任何位置的 `*.db*`、`*.sqlite*`、`*.avi`、`*.mp4`、`*.task`、`*.env`（`*.env.example` 除外）
- 紀錄頁、成員頁、設定頁都需要 PIN（含人臉影像）
- 大型語言模型或任何自動化**不能自己開鎖**（居家助理規劃的原則）
