# CLAUDE.md — 智慧門鎖交接文件

給接手的 Claude Code 讀的。先讀完這份，再讀 `README.md`（使用說明）和 `docs/`（規劃、**硬體接線：`docs/03_硬體接線.md`**）。

---

## 0. 進度、規劃、交接都在 Folio vault `folio/`（新 session 先看這裡）

這個 repo 用 [Folio](https://github.com/zelvet-velvet/folio) 管理進度（2026-11-16 起），**狀態不再寫在這份 CLAUDE.md**：
- **現在在做什麼、待辦**：`folio/LLM/DEV_STATUS.md`（Today / Yesterday / Ongoing）
- **階段規劃**：`folio/Quest/Plan.md`；各項規劃細節在 `folio/Quest/`（辨識模型比較、提高可靠度、對講機、Pi 4 維護）
- **待驗證項目（Pi 實測）**：`folio/Quest/Test Queue.md`
- **各版本的實作細節**（v2.6～v2.10、手機頁）：`folio/Impl/`；完成紀錄：`folio/LLM/PROGRESS_ARCHIVE.md`
- **repo、備份、私人資訊的規則**：`folio/Notes/Repo 與私人資訊.md`

指令（skill 本體在 `folio/LLM/skills/`，repo 根目錄 `.claude/skills/` 是 commit 進來的 stub）：`/cmd-sync`、`/cmd-rollover`、`/cmd-phase`、`/cmd-notes-sync`、`/cmd-journal`、`/cmd-report`、`/cmd-features`，以及 `test-queue`、`git-workflow`、`mermaid-diagrams`。改 vault 筆記前先讀 `folio/LLM/LLM_GUIDE.md`。

**這個 repo 是 public，vault 也會公開**：私人資訊只放 `CLAUDE.local.md`（不進 git）；`folio/Journal/` 不進 git。

## 1. 專案是什麼

逢甲通訊系學弟的畢業專題：**人臉 + 手勢雙重驗證的智慧門鎖**。使用者（學長）負責軟體與整合，學弟負責 Arduino 與硬體。

```
Pi Camera 3 NoIR ─▶ Raspberry Pi 5 (4GB) ──USB 序列埠──▶ Arduino UNO ─▶ 繼電器 ─▶ 電磁鎖
                         │
                         └─ Flask 網頁（手機控制、紀錄、設定）── LINE 通知
```

- 人臉：YuNet 偵測 + SFace 128 維特徵（v2.6 起，取代 Haar + LBPH），距離 =（1 − cosine）× 100 < 50 算認得，`master` 連續 8 幀通過 → 進手勢階段
- 手勢：MediaPipe 0.10.18 Gesture Recognizer（模型下載失敗時改用 Hands + 規則）；v2.8 起每個主人可綁定一個手勢（`gestures.json`），沒綁定用讚 / YA；連續 5 幀 → 開鎖，比錯累計 15 幀 → 失敗冷卻
- `guest` 永遠不自動開鎖，只通知；陌生人逗留 ≥ 5 秒 → 哨兵錄影
- Arduino：送小寫 `unlock` / `lock` + `\n`，sketch 回 `Unlocked: ...` / `Locked`，8 秒後自動上鎖
- 接線：UNO D7 → 低電位觸發繼電器 IN；鎖用獨立 12V 電源經繼電器 COM/NO（鎖的種類與 NO/NC 還沒確認），詳見 `docs/03_硬體接線.md`

## 3. 常用指令

```bash
source .venv/bin/activate
python -m pytest -q                 # 183 個測試，不需要相機、Arduino 或人臉模型（conftest 的 FakeFaceEngine）（Mac：179 passed, 4 skipped，序列埠 pty 測試只在 Linux 跑）
python app.py run --sim             # 完整系統，用模擬 Arduino
python app.py records | cleanup | set-pin | set-gesture | check-face | test-camera | test-gesture | test-arduino | test-line | test-audio
# Mac 測對講：INTERCOM=sim python app.py run --sim；預設語音：python tools/make_sounds.py
# 只看版面（不用 Pi）：python tools/preview_web.py [port] → http://localhost:8800（手機用區網 IP），自動示範模式，PIN 1234
# 給手機試的單檔：python tools/build_single_html.py <輸出.html>（不要在 repo 根目錄開 http.server，會把 .git 和 CLAUDE.md 公開到區網）
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
  face.py              FaceEngine（YuNet+SFace，測試換成假的：make_engine）、辨識、capture、train（登錄特徵）、set_role、個人門檻、冒充者比對
  learning.py          回報誤認：AttemptLog、Hardening（report / undo / reset）
  bindings.py          GestureBindings：每個主人綁定的開鎖手勢（gestures.json）
  intercom.py          對講機：聽門口（arecord → raw PCM）、預錄語音（aplay，分聲道）、make_intercom
  gesture.py / arduino.py / camera.py / display.py / qr.py
  sentinel.py          哨兵：預錄緩衝、固定幀率取樣（迴圈慢時補幀）、背景寫檔 MJPG .avi
  storage.py           SQLite（records / events / presence / attempts）+ 容量管理（天數 / 總量 / SD 剩餘）
  line.py              LineClient（額度 10 分鐘快取、send：push/broadcast + retry key）、LineNotifier（訂閱事件、背景佇列、冷卻、低額度只發重要）
  web/server.py        Flask API + PinGuard（5 次錯鎖 60 秒）+ StreamRegistry
  web/templates/index.html, web/static/app.js, app.css, qrcode.min.js（MIT，內嵌）
  cli.py               指令、run() 組裝所有元件
tests/                 conftest.py 有 Harness（假辨識器 / 假手勢 / 假 Arduino / 假時鐘）
deploy/                smartlock.service、install_service.sh、smartlock.env.example
docs/                  01 居家助理未來規劃（概念階段）、02 智慧門鎖優化規劃、03 硬體接線、04 人臉辨識改版 v2.6（LBPH → SFace 的比較與改動）、05 資料污染與判定標準（回報誤認的原理、FAR/FRR、和強化學習的差別）
```

**不要破壞的規則**
- **使用者既有資料不能要求重拍**：`faces/`、`id_map.txt`（格式 `編號:名字:master|guest`）、`models/` 必須相容；`face_feats.npz` 可隨時從 `faces/` 重建；舊 `face.yml` 不再使用但不要刪。`train` 會保留既有的 guest/master 設定。
- **重新允許自動開鎖的條件**：鏡頭裡沒有臉持續 3 秒。使用者明確說過不要改這個規則。
- **guest 永遠不自動開鎖**。
- **綁定的開鎖手勢是第二道密碼**：門口畫面、`/api/status`、事件文字、PIN 鍵盤標題都不能寫出是哪個手勢；只有登入後的成員詳情（輸入 PIN 進過手勢頁後）看得到。
- **測試不能碰專案資料夾**：Harness 會把 `Config.base` 換成暫存目錄（Pi 上可能有真的 `pin.json`、`records/`）。
- 新功能優先用「訂閱事件」做，不要把邏輯塞進 `door.py`。
- 前端：整個 App `position: fixed`，**不要用 `100dvh`**（LINE 內建瀏覽器不支援，曾造成整頁捲動）；門鎖頁尺寸由 `fitDoor()` 依實際高度計算；每秒輪詢只改有變的欄位。
- 影像串流：同裝置只留一條、全部最多 4 條、10 分鐘換新、20 秒 socket timeout。瀏覽器同網域約 6 條連線上限，舊分頁曾讓網站看起來「當掉」。

**PIN 規則**
- 來源優先順序：`pin.json`（雜湊）> `smartlock.env` 的 `CONTROL_PIN` > 無
- 輸入一次後同手機免再輸入（session，改 PIN 後全部失效）；沒附 PIN 回 401 不算錯誤
- **變更主人/訪客、開鎖手勢每次都要 PIN，已登入也不算**（使用者明確要求）；錯誤鎖定仍保留。手勢是「進滾輪頁前」輸入，權限是「離開滾輪頁時」輸入
- **刪除成員**同樣每次都要 PIN，而且前端要先顯示「此操作無法復原」警告（使用者明確要求）；API 需帶 `confirm=<名字>`；不能刪最後一位成員。`face.delete_member` 先移出名單 → 刪照片 → 重新 train → `FaceRecognizer.swap()` 同時換特徵和名單（編號會重排）

## 5. LINE 通知（v2.5 已實作，以下為規格）

- 帳號已建好：官方帳號 `@your_line_id`，輕用量方案（每月 200 則，**廣播按好友人數計算**）
- token 已放在 Mac 開發資料夾的 `smartlock.env`（0600，gitignore），2026-11-04 用 quota 驗證有效（200 則、已用 0）。使用者的 Business ID 沒連動 LINE 帳號，所以 Console 看不到 Your user ID → 先用 **broadcast**；有 `LINE_USER_ID` 時改用 push
- 設定放 `smartlock.env`：`LINE_CHANNEL_TOKEN`、`LINE_BASIC_ID`、`LINE_USER_ID`（**token 絕不寫進程式或 git**）
- 做法：`LineNotifier` 訂閱事件
  - `stranger_detected`：只在 `data['notify']` 為 True 時發（哨兵已做 5 分鐘冷卻）
  - `guest_detected`、`door_failed`
  - push 後 `count_sent(1)`；broadcast 用量 = 好友數，改成讓額度快取失效下次重查；額度剩 20 以下只發重要的（door_failed、guest），0 則全停
  - 訪客（依名字）與 door_failed 有 `LINE_COOLDOWN`（預設 300 秒）；陌生人沿用哨兵的 notify 冷卻
  - 在背景執行緒發送，不能卡住辨識迴圈；失敗要記錄但不能讓系統掛掉
- **通知內容不放開鎖連結**；圖片需要公開 HTTPS 網址，先只發文字
- 測試在 `tests/test_line.py`：假的 opener（FakeLine），不會真的打 LINE API

之後：軟體移動偵測（低解析度幀差，沒動靜時跳過辨識），改前改後用 `htop`、`vcgencmd measure_temp` 量 CPU 與溫度，放進報告。

## 6. 環境

- **Pi**：Raspberry Pi OS Trixie 64-bit，hostname / 使用者 / IP 見 `CLAUDE.local.md`（不進 git），專案在 `~/door_pi_gesture`，`.venv` 用 uv 建（Python 3.11）
- **SSH**：Mac `~/.ssh/config` 有 `Host pi`（`raspberrypi.local`、金鑰 `~/.ssh/id_ed25519`），Claude 可直接 `ssh pi` / `rsync … pi:door_pi_gesture/`；要在 Pi 螢幕開視窗：`DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 SHOW_WINDOW=1`，背景跑用 `setsid nohup … > ~/smartlock_run.log`；停止用 `pkill -TERM -f "[a]pp.py run"`（會先上鎖）。**正式模式（會真的開鎖）啟動前先問使用者**
- Pi 的 `smartlock.env`：`SIM_ARDUINO=1`、LINE token 已設定；成員：`eric` 訪客、`jenny` 主人
- 網路：目前用 iPhone 熱點（Pi IP 會變）；學校 FCU-Auto 是 WPA2-Enterprise，放棄。之後可能改乙太網路 + Pi 開熱點（`nmcli device wifi hotspot`），還在確認場地
- 用 SSH 啟動要在 Pi 螢幕顯示視窗：前面加 `DISPLAY=:0`
- MediaPipe 0.10.18 沒有 Python 3.13 的 ARM wheel → Python 3.9–3.12
- 相機後端順序：rpicam-vid（YUV420 pipe）→ Picamera2 → USB（`CAMERA_INDEX`）
- **使用者的 Mac**：Python 3.11（`/opt/homebrew/bin/python3.11`）、uv 有裝（`/opt/homebrew/bin/uv`）；系統內建 `python3` 是 3.9.6，不要用它建 venv。建環境：`uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt`
- 部署到 Pi：複製 `smartlock/`、`app.py`、`tests/` 到 `~/door_pi_gesture`，資料檔不動；資料庫會自動升級

### Git / GitHub

- **兩個 Claude 共用這個 repo**：本地 Claude Code，和 claude.ai 的雲端 session（透過桌面 App 連結這個資料夾，以 git bundle 傳遞 commit）。對話內容不互通，**交接靠這份 CLAUDE.md 和 `git log`**；開工前先 `git log --oneline --all -10`。同一時間只在一個分支上改，不要兩邊同時改同一個分支

- Repo：`~/GITHUB/smart-door-lock`（使用者所有 git 專案都放在 `~/GITHUB/`）→ GitHub `Ehsu745/smart-door-lock`，**2026-11-16 起公開**：歷史已改寫清掉 Pi 帳號 / IP、LINE 官方帳號 ID、作者 email（改成 GitHub noreply），私人資訊放 `CLAUDE.local.md`
- `git log` 從最舊到最新完整保留：PC 測試版（10/14）→ Pi 單檔版 → 手勢單檔版（10/19、10/20）→ v2 模組化（11/03）→ v2.x（11/04）。舊版原始檔在 `~/FCU/CE_2026/AI_smartlock_playground/old_ver/`，不要動
- 公開前的舊歷史（含 `backup/pre-rebuild`、`v2.6-sface`、`v2.7-report-sface` 分支）只在本機備份，見 `folio/Notes/Repo 與私人資訊.md`
- Mac 上的開發 / 訓練資料夾：`~/FCU/CE_2026/AI_smartlock_playground/smartlock_project`（有 `.venv`、`faces/`、`face.yml`、`records/`），和 repo 分開；改完要同步進 repo 再 commit
- **資安待辦**：見 `CLAUDE.local.md`（不進 git）

## 7. 跟使用者合作的方式

- **開發流程：本地開發 → 測試驗證 → 才推 GitHub**；手機 UI 有改動時，進 main 前先用 `tools/build_single_html.py` 產生單檔 HTML 給使用者在手機上試
- **分支用完就刪**：功能測試通過直接合進 main，不保留功能分支

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
| 同一人在 master / Unknown 間跳動 | LBPH 時代的問題；v2.6 改 SFace 後待 Pi 實測。仍發生就在門口光線補拍（戴 / 不戴眼鏡都拍） |
| 陌生人被認成成員（2026-11-05 demo） | v2.6 改 SFace 修正；Pi 上待驗證 |
| NoIR 畫面偏紫 | 不影響辨識；手勢不夠準改 USB 相機 |
| SD 卡曾裂過、重灌 | 用 `sudo poweroff` 關機、5V/5A 電源，長期考慮 USB SSD |
| Wi-Fi 省電可能讓 Pi 斷線 | `nmcli connection modify <連線> 802-11-wireless.powersave 2` |

## 9. 隱私與機密

- 不要 commit：`sounds/`（可能是自己的聲音）、`faces/`、`face.yml`、`face_feats.npz`、`id_map.txt`、`gestures.json`、`models/`、`impostors/`、`thresholds.json`、`records/`、`pin.json`、`smartlock.env`（都在 `.gitignore`）；`.gitignore` 另外擋掉任何位置的 `*.db*`、`*.sqlite*`、`*.avi`、`*.mp4`、`*.task`、`*.env`（`*.env.example` 除外）
- 紀錄頁、成員頁、設定頁都需要 PIN（含人臉影像）
- 大型語言模型或任何自動化**不能自己開鎖**（居家助理規劃的原則）
