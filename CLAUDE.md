# CLAUDE.md — 智慧門鎖交接文件

給接手的 Claude Code 讀的。先讀完這份，再讀 `README.md`（使用說明）和 `docs/`（規劃、**硬體接線：`docs/03_硬體接線.md`**）。

---

## 0.0 交接：2026-11-14～16 本地 session 的狀態（新 session 先看這裡）

**本機資料夾**
- `~/GITHUB/smart-door-lock`：**改寫過歷史的乾淨版本**（`git filter-repo` 清掉 Pi 帳號 / IP、LINE 官方帳號 ID、本名、HackMD 那段、作者 gmail → GitHub noreply），remote 是 GitHub 上的 public repo `Ehsu745/smart-door-lock`。**在這份開發**；`user.email` 已設成 `39007338+Ehsu745@users.noreply.github.com`
- `~/GITHUB/smart-door-lock-old`：**舊歷史**（含私人資訊），已拿掉 remote，只留著參考；**不要再加 remote 或推上去**，也不要 `git add .`（它的 `.gitignore` 沒擋 `CLAUDE.local.md`）
- 改寫前完整備份：`~/GITHUB/smart-door-lock-before-public-20261114.bundle`（含私人資訊，不要上傳）
- 私人資訊在 `CLAUDE.local.md`（已被 `.gitignore` 擋住）；任何 commit 前確認沒有它列的私人字串
- LINE token 從來沒進過 git（掃過全部歷史）

**待辦（照順序）**
1. **Pi 4 忘記密碼**：SSH 金鑰能登入（`ssh pi4`），但 `sudo` 要密碼 → 加 Wi-Fi、關機都做不到。重設：SD 卡 `cmdline.txt` 行尾加 ` init=/bin/sh` → 開機 `mount -o remount,rw /` → `passwd 使用者` → `sync` → 拿掉 init 再開機
2. **Pi 4 加 Wi-Fi**（SSID / 密碼見 `CLAUDE.local.md`）：`nmcli connection add type wifi …` 需要 sudo 或在 Pi 桌面本機執行；只加設定、不要切換現在的連線（會斷 SSH）
3. `~/.ssh/config` 的 `Host pi4` 是寫死 IP，換網路會連不到 → 建議改成 mDNS 名稱（見 `CLAUDE.local.md`），先問使用者
4. Pi 4 關機：`systemctl poweroff` 也要 sudo，使用者自己從桌面關

**已完成**
- **GitHub 公開**（2026-11-16）：使用者在網頁刪掉舊的 `Ehsu745/smart-door-lock`、自己檢查內容後建新的 private repo 推 `main`、`face-accuracy` → 改成 public → 兩個資料夾改名（舊的 → `smart-door-lock-old`，public → `smart-door-lock`）。PR #1（`face-accuracy` → `main`）是使用者開的，還沒合併
- 準確度改善（§0.1 模型比較 `tools/bench_face_models.py`、§0.2 可靠度規劃）移到 `face-accuracy` 分支，main 只留指標；跑 `report` 前要 `git switch face-accuracy`（或用 worktree）
- Pi 4 同步到 **v2.10.0**（rsync `smartlock/ app.py tests/ tools/ requirements*.txt`，資料不動）：pytest **183 passed**（含 Linux 才跑的序列埠測試）；`test-audio` → 找不到音效卡（還沒接）；Pi 4 也還沒接 USB 鏡頭。成員只有 `me`（master）
- 刪了本機 `v2.8-gesture-tmp`（已合進 main，bundle 裡有）

## 0. 下一步規劃（2026-11-07～11-14 與 claude.ai 雲端 session 討論的結論，給本地 Claude Code）

以下是使用者和雲端 session 討論後的結論，**還沒實作**。開工前先跟使用者確認要做哪一項；每項做完照 §7 的流程（本地開發 → 測試 → 單檔 HTML 給手機試（有改 UI 時）→ 進 main），並把該項移到「§2 目前狀態」。

### 0.1 辨識模型比較：SFace vs ArcFace（使用者想先做）

> **2026-11-14 進度（暫緩，使用者改先做 0.3）**：已在 Mac 開發資料夾 `~/FCU/CE_2026/AI_smartlock_playground/smartlock_project` 準備好（onnxruntime 1.32、`models/insightface/buffalo_s|l`、`~/lfw_funneled` 5,760 人），在那個資料夾跑 `python ~/GITHUB/smart-door-lock/tools/bench_face_models.py …`（腳本用 cwd 找 models/、faces/）。`speed`（Mac 單執行緒 ms）：SFace 5.28、mbf 3.66、r50 38.9；`extract` 完成（9,674 張，`bench_out/feats_*.npz` 在開發資料夾）；**`report` 還沒跑**。相簿只有 `me` 30 張（灰階），門鎖情境看 `*_gray`

**起因**：朋友建議 InsightFace 架構（SCRFD-500M → ArcFace MobileFaceNet `w600k_mbf` → numpy；他投影片上的 2d106det 是會議中偵測說話者用的，使用者說可忽略；LanceDB 在幾百個向量的規模用不上，numpy 就夠）。這跟現在的 YuNet → SFace → numpy 是**同一個架構換零件**，值不值得換要用數據決定。

**已確認**
- 偵測器換 SCRFD 沒有意義：單執行緒 320×240 SCRFD-500M 4.4 ms、YuNet 4.1 ms；門口近距離正臉兩者都夠；SCRFD 還要 onnxruntime ＋ 自寫輸出解碼。**要換也只換辨識模型，保留 YuNet**（YuNet 的 5 點可直接對齊 ArcFace，`tools/bench_face_models.py` 就是這樣接）
- 特徵擷取速度（雲端 x86，單執行緒，只算 embedding）：SFace 9.4 ms、ArcFace mbf 14.5 ms、ArcFace r50 259 ms → **r50 在 Pi 上每張 0.5 秒以上，排除**；只考慮 mbf
- **授權**：InsightFace 程式碼 MIT，但**預訓練權重（buffalo_s / buffalo_l，含 SCRFD、ArcFace）限非商業研究用途**。畢業專題可以，報告裡要註明

**要做的**
1. 照 `tools/bench_face_models.py` 檔頭準備：`onnxruntime`、InsightFace 權重放 `models/insightface/`、**LFW 在 Mac 上下載**（`curl -L -o ~/lfw-funneled.tgz https://ndownloader.figshare.com/files/5976015`，約 230 MB；雲端 session 的網路連不到，使用者 11/07 還沒下載）
2. `speed` → `extract ~/lfw_funneled`（每人最多 6 張，約 1 萬張）→ `report`，輸出 `bench_out/report.json`（已 gitignore）
3. 比較重點：
   - LFW 本人配對 vs 陌生人配對：TAR @ FAR = 1e-3 / 1e-4 / 1e-5、EER
   - 門鎖情境（`faces/` 30 張當相簿、LFW 每人第一張當陌生人）：本人最差距離 vs 最像的陌生人距離，**兩者的差距（安全邊際）**才是重點
   - **color vs gray**：門鎖目前全程灰階（舊照片是灰階 Haar 裁切），順便回答「SFace 吃灰階損失多少」這個從 v2.6 就沒驗證的問題
4. **判斷規則**：ArcFace mbf 的安全邊際明顯大於 SFace（例如 TAR@1e-4 高好幾個百分點、或門鎖情境邊際大很多）才換；差不多就留 SFace，把力氣放在 0.2 的活體偵測
5. 如果要換（另外問使用者）：只改 `face.py` 的特徵擷取；距離尺度會變 → `FACE_THRESHOLD`、`FACE_REPORT_FLOOR` / `MARGIN` 要依報告重定，`thresholds.json` 的 `scale` 標記改成新值（舊檔自動忽略的機制已經有）；`face_feats.npz` 和 `impostors/` 特徵會從照片自動重建，**不用重拍**；`check-face` 的「穩定」標準（中位數 < 40）也要跟著改
6. 報告寫法：0 次誤認寫 rule of three 上限（3 / N），不要寫 0%

### 0.2 提高可靠度（依投入少、效果大排序）

使用者朋友批評「CV 做門禁不可靠」，雲端 session 的評估：**純 2D 影像的人臉辨識確實防不了照片 / 螢幕翻拍**（目前拿 eric 照片 + 自己的手比對的手勢就可能通過；v2.8 綁定手勢讓攻擊者還要知道手勢，但人臉這關仍會被騙），但解法是**加感測、不是放棄 CV**。

**第一優先（便宜、效果最大）**
| 項目 | 做法 | 備註 |
|---|---|---|
| 控制器裝在門內 | 門外只留鏡頭；Pi、Arduino、繼電器、電源在門內 | 0 元。最大的實體漏洞是「拆面板短路繼電器」，不是人臉 |
| 門磁感應器 | 磁簧開關接 Arduino 輸入腳，序列埠回報門的開關狀態 → 事件 `door_opened` / `door_forced`（沒開鎖門卻開了）/ `door_left_open` → LINE | 約 50 元；要學弟改 sketch，協定要加一行狀態回報（先跟使用者確認格式） |
| 連續失敗鎖定 | 人臉通過但手勢錯 N 次 → 暫停自動開鎖幾分鐘 + LINE | 用訂閱事件做（`gesture_wrong`），不要塞進 `door.py`；N 和時間先問使用者 |
| 用實測資料定門檻 | v2.7 的 `attempts` 表已經存本人每次的距離；累積後算門口真實的 FRR，再檢討 50 / 35 / 3 | 可以做成 `records` 指令的統計或設定頁一張卡 |

**第二優先：活體偵測（防照片 / 螢幕）**——先做不用買東西的，用攻擊測試的數據決定要不要買硬體
| 項目 | 做法 | 能擋 / 限制 |
|---|---|---|
| 軟體防偽模型 | Silent-Face-Anti-Spoofing（MiniFASNet，ONNX，小，Pi 跑得動），接在人臉通過之前 | 擋印刷照片和多數螢幕翻拍；換光線 / 相機會掉準確度。**授權先確認** |
| 眨眼偵測 | MediaPipe Face Mesh（已經裝了 MediaPipe）或 InsightFace 2d106det 算眼睛開合比例（EAR），人臉通過前要看到一次眨眼 | 擋照片；擋不住影片重播 |
| 測距（ToF） | VL53L5CX（8×8 多點，約 500～800 元，I2C 接 Pi）：① 有人進 1.5 m 才跑辨識（= 規劃第 5 項，省 CPU / 降溫）② 只接受 0.4～1 m 內的辨識 ③ 距離 vs 臉的像素寬做尺寸一致性（手機螢幕上的臉太小）④ 臉的區域是不是平面 | 戶外陽光會干擾；要和相機視野校正對位；擋不住立體面具 |
| 雙鏡頭 IR | Pi 5 有兩個 CSI：一般相機認人，NoIR ＋ 850nm 濾光片 ＋ IR 補光燈判斷真假（螢幕不發紅外線） | 約 500～1,500 元；**只有 NoIR 不加濾光片沒用**（它也看得到可見光） |

**驗證方式（報告與履歷都用得上）**：做攻擊測試集——印出的照片、手機螢幕、平板播影片，每種 20～50 次，記錄被騙過幾次（ISO/IEC 30107-3 的 APCER 概念）；加上門口實測 FRR。每加一道防線前後各跑一輪。

**第三優先（系統不出事）**
- 確認鎖的種類：fail-secure（斷電鎖住）或 fail-safe（斷電打開，逃生門通常要求）；**一定要有實體鑰匙備援**
- Pi 用 UPS 模組、鎖的 12V 加電池；SD 卡改 USB SSD 或 overlayfs 唯讀根目錄
- systemd `WatchdogSec` + 硬體看門狗；相機無畫面 / Arduino 無回應 → LINE
- HTTPS（Tailscale）+ PIN 改 6 位：目前 PIN 在 Wi-Fi 上是明文

**報告措辭（建議）**：寫清楚限制比聲稱很安全有說服力——「隨機陌生人單幀誤認率上限約 0.054%（LFW 5,524 人）；目前不防照片攻擊，下一步以 ToF / 紅外線做活體偵測；控制器裝在門內」。

### 0.3 對講機（學弟實驗室教授指定的設備）

- **設備**：Waveshare「USB TO AUDIO」免驅動 USB 音效卡（內建 2 顆麥克風、不支援外接麥克風；立體聲 BTL 輸出每聲道約 2.6 W @ 4 Ω；Pi 上顯示為「USB PnP Audio Device」）＋兩顆箱體喇叭（雙單體小音箱，**品牌型號與阻抗還沒確認**，看背面標示是 4 Ω 還是 8 Ω；8 Ω 也能用、音量較小）。說明文件：https://www.waveshare.com/wiki/USB_TO_AUDIO
- **接線**：排針 `VOLP/VOLN` 左聲道、`VORP/VORN` 右聲道，一顆喇叭各接一組；**BTL 的 N 不是地線，不能接 GND、兩聲道負端不能並在一起**
- **兩顆喇叭的用法**：左聲道門外（對訪客說話）、右聲道室內（門鈴聲 / 提示），同一張卡分聲道播放
- **回音**：麥克風和喇叭靠很近 → 先做**半雙工（按住說話，講話時麥克風靜音）**；全雙工之後再用 PipeWire 的 echo-cancel 模組
- **分階段**（手機用麥克風一定要 HTTPS，所以先做不需要的）：
  1. ✅ **聽門口聲音**（v2.10，見 §2）
  2. ✅ **預錄語音回覆**（v2.10，見 §2）
  3. **門鈴**：按鈕接 Arduino 或 Pi GPIO → 事件 `doorbell` → LINE 通知 + 室內喇叭響（通知不放開鎖連結）
  4. **手機對門口講話**：需要 HTTPS（Tailscale + ProxyFix），等 HTTPS 做了再說
- **Pi 上的驗證**（還沒做；v2.10 起可直接 `python app.py test-audio`，下面是手動版）：
  ```bash
  arecord -l && aplay -l                                   # 找 USB PnP 的卡號，下面假設 2
  arecord -D plughw:2,0 -f S16_LE -r 16000 -c 2 -d 5 test.wav && aplay -D plughw:2,0 test.wav
  alsamixer                                                # F6 選 USB 卡，調麥克風增益
  vcgencmd get_throttled                                   # 喇叭最大聲時不是 0x0 = 欠壓
  ```
  確認門口 1 m 外講話是否清楚、最大音量時有沒有欠壓
- 音效卡和喇叭都不防水，內建麥克風會收風聲 → 裝在有遮蔽的地方

### 0.4 雜項

- `~/FCU/CE_2026/AI_smartlock_playground/bench_input.tgz`（雲端 session 傳模型與 `faces/` 用的暫存檔，含人臉照片）和 `ref_cloud_gesture_binding/` 都可以刪，刪之前問使用者
- `.gitignore` 已加 `bench_out/`

---

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

## 2. 目前狀態（v2.10.0）

**v2.10 對講機階段 1、2（2026-11-14，本地 Claude Code）**：手機聽門口、播放預錄語音。**還沒進 main、還沒上 Pi、還沒接音效卡實測**
- `intercom.py`：用系統 `arecord` / `aplay`（alsa-utils，不加 Python 套件）。自動找 `arecord -l` 裡名稱含 USB（優先 PnP）的卡 → `plughw:N,0`；`AUDIO_DEVICE` 可指定（被 PipeWire 占用時用 `default`）。`INTERCOM=auto|1|0|sim`，沒有卡 → `make_intercom` 回傳 None，網頁不顯示按鈕
- 收音：mono 16 kHz S16LE，0.1 秒一塊；**有人聽才開 arecord、最後一人離開就關**（`_mic_busy` 鎖確保同時只有一個 arecord）；每個聽眾一個佇列（3 秒），滿了丟最舊的
- 播放：`sounds/*.wav`（16-bit PCM、≤ 30 秒，gitignore），檔名 = 按鈕文字、開頭編號排序；單聲道轉立體聲只放 `INTERCOM_CHANNEL`（預設 left = 門外）；同時只播一段；clip id 不能含 `/`、`\`、開頭 `.`
- 網頁：`GET /api/intercom`（登入）、`GET /audio_feed`（登入；raw PCM、`X-Sample-Rate`；另一個 `StreamRegistry`，規則同影像）、`POST /api/intercom/play`（登入或附 PIN，同 `/api/action`；忙碌 409）；`/api/status.intercom` = `{listen, reply, playing}`（不給聽眾數）；設定頁「系統」多一列對講機
- 事件：`intercom_listen`（同 IP 15 分鐘內只記一次，串流 10 分鐘重連不重複記）、`intercom_reply`；時間軸灰點（`talk`）
- 前端：影像右下角疊「聆聽 / 回覆」（不改 `fitDoor` 的高度分配；矮螢幕 compact 只留圖示）；聆聽 = fetch ReadableStream → Web Audio，緩衝 0.25 秒、超前 1 秒就丟（實測延遲約 0.3 秒）；`AudioContext` 在按鈕 click 裡建立 / resume（iOS 規定），停止時不關 context（PIN 之後重開不是 user gesture）；`navigator.audioSession.type = 'playback'` 讓 iPhone 靜音開關打開也有聲音（Safari 16.4+）；離開門鎖頁 / 背景 / pagehide 就停
- 順便修：PIN 鍵盤的 Enter 沒 `preventDefault`，桌機焦點還在原按鈕時會再按一次那個按鈕（聆聽會無限跳 PIN）
- `test-audio`：找卡 → 錄 5 秒印 RMS / 峰值 → 左、右聲道各播 → `vcgencmd get_throttled`；`tools/make_sounds.py`：Mac `say -v Meijia` + `afconvert` 產生三段預設語音
- 測試 156 → 183（`tests/test_intercom.py`，假麥克風 / 假喇叭）；Playwright Chromium + WebKit、375×548 / 390×664：PIN → 聆聽（AudioContext running、音量條動）→ 回覆（播放中標示、播完解除）→ 停止 / 換頁後 `/api/health` 的 `audio_streams` 歸零；示範模式也有對講（沒有聲音）。使用者 2026-11-14 用手機連 Mac 上的模擬伺服器（真 Flask + `SimMic` 嗶聲 + `SimSpeaker` afplay）實測：手機聽得到嗶聲、回覆從 Mac 喇叭播出
- **待驗證（Pi）**：PipeWire 會不會占住 plughw、USB 卡的 1 聲道錄音、門口 1 m 收音、喇叭最大聲時欠壓、iPhone / LINE 內建瀏覽器的 fetch 串流與 Web Audio


**v2.9 人臉檢查與分次補拍（2026-11-07）**
- `capture 名字 [張數]`（1～200，預設 30）；`check-face [秒數]`（5～300，預設 20）：每幀取最大的臉，印距離分布、各門檻通過率、達到連續 `required_frames` 幾次；結論 穩定（中位數 < 40 且開得了）/ 不穩 / 幾乎認不出來。不存照片
- **名字只看「認得」的幀**：`FaceResult.person` 沒過門檻時仍是最接近的成員，直接拿來建議 `capture` 會把陌生人登錄成那個人（模板污染）；認不出來時只寫「你的名字」
- 起因：Mac 內建鏡頭量本人（登錄照片是 Pi 5 NoIR 一次連拍的 30 張 162×162 灰階緊裁切、全戴眼鏡）：戴眼鏡中位 46～49、門檻 50 通過 64～79%；不戴 52.7、27%。跟 LFW 最像的陌生人（54）、端對端誤認（52.8）太近 → **不調高門檻**，改成每次換地方架設就跑 check-face、不穩就在 Pi 上 `capture 名字 8` 補拍（相機位置還沒固定、沒有門框，環境多樣反而是優點）
- Pi 4（自己的，`ssh pi4`，USB webcam 還沒有）：Trixie 64-bit、pytest 全過；identify 126 ms、手勢 117 ms、約 65°C 沒降頻


**v2.8 手勢綁定（2026-11-07，本地 Claude Code；參考雲端 session 的 LBPH 版 patch 移植到 SFace 版）**：已進 `main`，`v2.6-sface` / `v2.7-report-sface` 分支已合併
- `bindings.py`：`GestureBindings`（`gestures.json` 0600、mtime 變了自動重讀、壞檔當沒綁定）；可選 MediaPipe 7 種；`cli.py set-gesture [名字 手勢|none]`
- `door.py`：人臉通過時決定 `_required`（綁定的那一個，或 `ALLOWED_GESTURES`）；比錯（信心夠且不是 None）**累計** `GESTURE_WRONG_FRAMES`=15 → `gesture_wrong` + 冷卻 `retry_cooldown`；沒比手勢仍走 `gesture_timeout`
- **不洩漏（使用者規格）**：門口畫面寫 `your gesture`、綁定時不畫目前看到的手勢名稱；`/api/status` 只有 `target_bound`，綁定時 `gesture` / `gesture_score` 一律 None（**用 `_bound` 判斷，不是 phase**：開鎖那一幀已切回 face，但 g_name 還是答案）；開鎖事件寫「綁定手勢」；`gesture_changed` 事件與 PIN 鍵盤標題都不寫手勢；骨架不變色
- 銜接 v2.7：`learning.CLOSE` / `server.ATTEMPT_EVENTS` 加 `gesture_wrong`（時間軸可回報）；前端「異常」篩選加 `gesture_wrong`
- 網頁：`POST /api/users/<name>/gesture`（每次 PIN、沒變不記事件）、`GET /api/gestures`（需登入）、`/api/users` 帶 `gesture`（需登入）；刪除成員一起刪綁定
- **手機 UI（使用者在手機上確認過）**
  - 成員頁：主人 / 訪客說明收在標題列右側 ⓘ（`toggleInfo`，蓋在清單上）；清單下方只有文字按鈕「如何新增成員」（抽屜裡分步驟＋指令）
  - 成員詳情：**「管理」在上、「資料」在下**；開門方式顯示「人臉＋綁定手勢」或「人臉＋讚 / YA」
  - 開鎖手勢列：預設遮住 `••••`＋眼睛斜線圖示（不是按鈕）；點這列 → **先輸入 PIN**（用 `/api/login` 驗證，錯誤照樣計入鎖定）→ 滾輪頁；離開（‹、✕、點背景、Esc）有改就**立即送出**、不再跳 PIN，不改直接離開；PIN 只留在那一頁的 closure。之後回到詳情看得到手勢（眼睛睜開），**離開詳情（closeSheet、換成別人的詳情）立即遮回去**（`revealed`）
  - 修改權限維持舊做法：滾輪頁離開時有改才跳 PIN
  - **滾輪完全由程式控制**（`wheelMount` / `wheelGo`）：`overflow: hidden` + `touch-action: none`，pointer 拖曳、放開後依速度決定格數（< 0.6 px/ms 不加、最多 3 格），ease-out 一次到位不彈跳；滑鼠滾輪 / 觸控板一格一格。原因：iOS 的 scroll-snap 加補對齊會上下彈跳、Safari 桌機版滑鼠滾輪不吸附。**拖動超過 4px 才 setPointerCapture**，否則項目收不到 click。列高 36px（`WHEEL_ROW` 與 CSS 要一致），沒選中的同色、淡 50%
  - `#sheetBody` 可捲動（內容比抽屜高時不會被裁掉）
- 測試 136 → 151；Playwright（Chromium / WebKit / Firefox）檢查過拖曳單調、對齊、26 項流程；使用者 2026-11-07 在 Mac `run --sim` 實測通過。**還沒上 Pi**
- 參考原始碼在 `~/FCU/CE_2026/AI_smartlock_playground/ref_cloud_gesture_binding/`（已移植完，可以刪）


**v2.7 回報誤認（2026-11-07，claude.ai 雲端 session 做的，分支 `v2.7-report-sface`，從 `v2.6-sface` 開出）**
- 時間軸上開鎖 / 手勢逾時旁有「這不是 某某？」→ 說明 → PIN（**每次都要 PIN**，同變更權限）→ 立即生效，不用重啟
- `learning.py`：`AttemptLog`（訂閱事件，每次 master 人臉通過記一筆：人臉裁切、連續通過幾幀中最小距離、結果）、`Hardening`（report / undo / reset）
- 兩道防線：① 個人門檻 `thresholds.json` = max(`FACE_REPORT_FLOOR`=35, 最像的已回報冒充者距離 − `FACE_REPORT_MARGIN`=3)，只會比預設嚴；檔案有 `scale: sface` 標記，LBPH 時代的舊檔自動忽略 ② 冒充者比對 `impostors/*.jpg`：新臉比任何成員照片都更像某張冒充者照片 → 當陌生人（`FaceResult.impostor`）
- 取消回報：拿掉那筆特徵、重算門檻（不用 retrain）；成員頁「恢復預設判定標準」（PIN）；刪除成員會清掉他的門檻與嘗試紀錄（冒充者照片保留）
- `door.py` 只多了 attempt 編號與 `_crop`（底線開頭的欄位不寫進 SQLite）；`gesture_cancel` / `gesture_timeout` / `unlock` / `door_failed` 帶 `attempt`
- 人臉圖跟錄影一樣保留 `RECORD_MAX_DAYS`；回報過的那筆距離永久保留（門檻靠它重算）
- **成員相簿永遠不會被自動加入照片**（理由見 `docs/05_資料污染與判定標準.md`）
- margin 3 / floor 35 是依 v2.6 LFW 數據（本人最差距離 16、最像的陌生人 54）定的，**還沒用 Pi 門口資料驗證**
- 前端已用 Playwright 在 375×548、390×664 看過（示範模式）；還沒在 Pi 上實測


**v2.6 人臉辨識改版（2026-11-05）**：學弟 demo 時陌生人被認成 eric。用 LFW 5,524 位陌生人比較（單幀）：LBPH 門檻 70 誤認 8.6%；SFace 門檻 50 為 0 / 5,524（95% 信心上限約 0.054%，報告不要寫 0%；門檻 55 時 1000 人中 1 人）。LBPH 誤認最嚴重的人多數沒戴眼鏡 → 主因是 LBPH 本身，眼鏡是次要。
- 使用者決定：**只用 Pi 自己拍的照片**、**不做 LBPH / SFace 切換**。PC 端訓練、PIN 開門後回收照片（需防模板污染）是之後居家助理的方向，還沒做
- `train` 不訓練網路，只把 `faces/` 照片轉成特徵存 `face_feats.npz`；沒有這個檔時 `FaceRecognizer.load` 自動建立（舊資料免重拍）
- 模型自動下載到 `models/`（huggingface opencv/…，有 SHA-256 檢查）；OpenCV 需 ≥ 4.8
- 辨識和登錄都用灰階（舊照片是灰階 Haar 裁切）；capture 現在裁切時多留 1/4 邊
- **還沒在 Pi 上實測**：速度（Pi 5 / Pi 4）、門口光線下本人的距離分布、戴與不戴眼鏡

| 優化規劃項目 | 狀態 |
|---|---|
| 1. 模組化 + 狀態機測試 | ✅ |
| 2. systemd 背景服務 | ✅（`deploy/`） |
| 3. 哨兵錄影 + SQLite + 控制頁 | ✅ |
| 4. LINE 文字通知 | ✅ v2.5（`LineNotifier`；token 已驗證有效，真實發送待使用者確認後用 `test-line` 測） |
| 5. 軟體移動偵測 + 效能量測 | ⬜ |

額外完成：手機單頁 App（門鎖 / 成員 / 紀錄 / 設定）、PIN 雜湊與網頁變更、網頁變更主人/訪客、網頁刪除成員（v2.4：警告無法復原 → PIN → 刪照片並重新訓練）、串流連線數修正（網站「當掉」問題）、依手機尺寸排版。

**2026-11-04 Pi 實測（v2.5，`run --sim`）通過**：v2.5 直接讀舊資料（不用重訓）、手機控制頁、網頁變更權限、LINE 測試訊息 / 訪客通知（含 5 分鐘冷卻）/ 陌生人錄影 #1（31 秒）與通知、正常結束會先上鎖。Pi 上舊版完整備份在 `~/door_pi_gesture_backup_1020`。

**還沒實測的**：正式模式接 Arduino（當天 `lsusb` 看不到 Arduino，只有滑鼠 → 檢查 USB 線是否為資料線）、網頁刪除成員在 Pi 上重新訓練的耗時、systemd 服務、iPhone / LINE 內建瀏覽器的滾輪手感。

**手機頁改版（2026-11-04，commit 5bb1e15）**
- 成員詳情：「資料」卡（開門方式 / 訓練照片 / 最近出現）+「管理」卡只有「修改權限 ›」
- 修改權限子頁：標題列 ‹ 修改權限 … 頭像 ⓘ ✕；iOS 風格滾輪（主人 / 訪客）；**沒有套用鍵，離開（‹、✕、點背景、Esc）時有改才跳 PIN 生效，PIN 鍵盤按 ✕ = 放棄**（使用者同意的做法：避免滾動時一直跳 PIN）；ⓘ 是主人 / 訪客差別說明（蓋在滾輪上）；滾輪下方只在有改時顯示「離開這頁時輸入 PIN 即生效」；最下方分隔線後是「刪除成員資料」
- 門鎖分頁：狀態列和影像同寬（`--cam-w`）；手勢提示只顯示中文名（讚 / YA），窄螢幕才放得下
- 預覽：`python tools/preview_web.py`（port 8800，示範模式、no-cache，手機用區網 IP 開）

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

- Repo：`~/GITHUB/smart-door-lock`（使用者所有 git 專案都放在 `~/GITHUB/`）→ GitHub `Ehsu745/smart-door-lock`，**2026-11-14 起準備公開**：歷史已改寫清掉 Pi 帳號 / IP、LINE 官方帳號 ID、作者 email（改成 GitHub noreply），私人資訊放 `CLAUDE.local.md`
- `git log` 從最舊到最新完整保留：PC 測試版（10/14）→ Pi 單檔版 → 手勢單檔版（10/19、10/20）→ v2 模組化（11/03）→ v2.x（11/04）。舊版原始檔在 `~/FCU/CE_2026/AI_smartlock_playground/old_ver/`，不要動
- `backup/pre-rebuild`、`v2.6-sface`、`v2.7-report-sface` 分支在公開前刪除（舊歷史含私人資訊；完整備份見 `CLAUDE.local.md`）
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
