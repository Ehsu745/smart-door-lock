---
sticker: lucide//list-checks
up: "[[Quest]]"
---
Test checklists, one group per branch or batch of work, in the order it was done. Groups that are merged with every box ticked move to [[Test Queue Archive]] (their Index row stays). Rules: skill `test-queue`.

## Index

| Group | Topic | Repo | Branch | Status |
| :--- | :--- | :--- | :--- | :--- |
| A | v2.6 人臉 SFace | smart-door-lock | main | merged，Pi 5 已部署，等相機裝上門框再測 |
| B | v2.7 回報誤認 | smart-door-lock | main | merged，Pi 5 已部署，待測 |
| C | v2.8 手勢綁定 | smart-door-lock | main | merged，Pi 5 已部署，待測 |
| D | v2.9 check-face / 補拍 | smart-door-lock | main | merged，Pi 5 已部署，等相機裝上門框 |
| E | v2.10 對講機階段 1、2 | smart-door-lock | main | merged，Pi 5 部分測過（E1、E2、E4） |
| F | 正式模式與部署 | smart-door-lock | main | Pi 5 待測；F1 Arduino 暫緩到裝門鎖（之前用 `--sim`） |
| H | v2.11 按住說話 + HTTPS | smart-door-lock | `feat/https-talk` | Mac 測過，Pi 5 待測 |
| G | 對講機喇叭音量、聆聽免 PIN | smart-door-lock | `fix/intercom-volume`、`feat/listen-without-pin` | merged `e642f8c`，Pi 5 部分測過（G1） |

## 2026-11-16 · Pi 5 驗收（[[Plan]] Phase 1）

> [!todo]- Group A · v2.6 人臉 SFace（smart-door-lock）
> **Branch** `main` · **Status** merged（`v2.6-sface` 已合併）；Mac 與 LFW 測過，Pi 5 沒測
> **Why**：學弟 demo 時陌生人被認成 eric；LBPH 門檻 70 誤認 8.6%，SFace 門檻 50 為 0 / 5,524。要確認門口實際條件下本人也認得到。
> - [ ] **A1 速度**：Pi 5 上 identify / 手勢每幀耗時
>     - Expect：與 Pi 4（identify 126 ms、手勢 117 ms）同級或更快，不降頻
>     - > Pi 5（2026-11-17）：`test-camera` 沒人 30 fps、有人辨識時約 18 fps，`throttled=0x0`；還沒量分項 ms
> - [ ] **A2 門口本人距離分布**：門口光線下 `check-face`
>     - Expect：中位數 < 40，門檻 50 通過率高、連續 8 幀達得到
>     - > Pi 5（2026-11-17）：舊照片自動轉 SFace（eric 30、jenny 30 張）；桌上 `test-camera` eric 距離多在 23～29、一格 51（Unknown），逆光、臉太近。相機還沒固定在門框，正式量測延到裝好
> - [ ] **A3 戴 / 不戴眼鏡**
>     - Expect：兩種都開得了；不穩就在門口光線補拍（`capture 名字 8`）
> - [ ] **A4 陌生人不被認成成員**（demo 那次的情境重現）
>     - Expect：陌生人一律 Unknown
> - [ ] **A5 不在 master / Unknown 間跳動**（LBPH 時代的問題）
>     - Expect：本人站定時結果穩定

> [!todo]- Group B · v2.7 回報誤認（smart-door-lock）
> **Branch** `main` · **Status** merged（`v2.7-report-sface` 已合併）；前端只在 Playwright 示範模式看過
> **Why**：margin 3 / floor 35 是依 v2.6 LFW 數據（本人最差 16、最像的陌生人 54）定的，要用 Pi 門口資料驗證。
> - [ ] **B1 回報流程**：時間軸「這不是 某某？」→ 說明 → PIN → 立即生效
>     - Expect：個人門檻變嚴、該冒充者下次被當陌生人
> - [ ] **B2 取消回報 / 恢復預設判定標準**
>     - Expect：門檻重算回原值，不用 retrain
> - [ ] **B3 門檻驗證**：累積門口 `attempts` 後看本人距離分布
>     - Expect：floor 35 不會讓本人常常開不了門

> [!todo]- Group C · v2.8 手勢綁定（smart-door-lock）
> **Branch** `main` · **Status** merged；Mac `run --sim` 使用者實測通過（2026-11-07），Pi 沒測
> **Why**：人臉可能被照片騙，綁定手勢讓攻擊者還要知道手勢。
> - [ ] **C1 Pi 上綁定與開門**：綁定手勢開得了、比錯累計 15 幀 → `gesture_wrong` + 冷卻
>     - Expect：門口畫面、`/api/status`、事件文字都不洩漏是哪個手勢
> - [ ] **C2 滾輪手感**：iPhone Safari、LINE 內建瀏覽器拖曳與點選
>     - Expect：不彈跳、對齊、點得到項目

> [!todo]- Group D · v2.9 check-face / 補拍（smart-door-lock）
> **Branch** `main` · **Status** merged；Pi 4 pytest 全過
> **Why**：Mac 鏡頭量本人距離離陌生人太近，決定不調門檻、改成每次換地方架設就跑 check-face。
> - [ ] **D1 Pi 5 `check-face`**：門口架設後跑一次
>     - Expect：結論「穩定」；不穩就 `capture 名字 8` 補拍後再跑

> [!todo]- Group E · v2.10 對講機階段 1、2（smart-door-lock）
> **Branch** `main` · **Status** merged；Mac 模擬伺服器使用者實測通過（2026-11-14）；Pi 4 同步 v2.10，pytest 183 passed；Pi 5 同步 v2.10（2026-11-17），pytest 183 passed，音效卡實測見下
> **Why**：學弟實驗室教授指定的 USB 音效卡 + 兩顆喇叭；細節見 [[對講機]]。
> - [x] **E1 `python app.py test-audio` 找得到卡**
>     - Expect：列出 USB PnP 卡、錄 5 秒有 RMS、左右聲道各播一次
>     - > Pi 4（2026-11-15）：找不到音效卡（還沒接）
>     - > Pi 5（2026-11-17）：第一個 USB 孔完全沒反應（dmesg 沒有插入紀錄），換孔後找到 `plughw:2,0`；錄 5 秒 RMS 315 / 峰值 2293（使用者在公共空間小聲說話，增益已是最大），使用者回報播放聽得到。喇叭原本完全沒聲音：音量刻度錯（預設 62% ≈ 靜音），插到 Mac 正常，修正見 Group G
> - [x] **E2 PipeWire 會不會占住 plughw**
>     - Expect：被占用時 `AUDIO_DEVICE=default` 可用
>     - > Pi 5（2026-11-17）：PipeWire 在跑，`test-audio` 和 `run --sim` 直接用 `plughw:2,0` 都沒被占住，不用設 `AUDIO_DEVICE`
> - [ ] **E3 USB 卡 1 聲道錄音、門口 1 m 收音**
>     - Expect：手機聽得清楚
>     - > Pi 5（2026-11-17）：桌上近距離手機聆聽良好（使用者回報）；門口 1 m 還沒測（音效卡還沒裝到門口）
> - [x] **E4 喇叭最大聲時欠壓**：`vcgencmd get_throttled`
>     - Expect：`0x0`
>     - > Pi 5（2026-11-17）：喇叭 100% 播放後 `throttled=0x0`
> - [ ] **E5 iPhone / LINE 內建瀏覽器**：fetch 串流 + Web Audio 聆聽、回覆播放
>     - Expect：聽得到、延遲約 0.3 秒；靜音開關打開也有聲音
>     - > Pi 5（2026-11-17）：使用者手機實測聆聽、回覆都良好；聆聽時按回覆會聽到一點回音（麥克風和喇叭很近），使用者認為可接受。LINE 內建瀏覽器、靜音開關、延遲還沒確認

> [!todo]- Group F · 正式模式與部署（smart-door-lock）
> **Branch** `main` · **Status** 還沒測
> **Why**：11/04 Pi 實測只跑 `--sim`，正式模式（會真的開鎖）一直沒驗證；啟動前先問使用者。
> - [-] **F1 Arduino 正式模式（暫緩到裝門鎖）**：`lsusb` 看得到 UNO、`test-arduino` 開 / 關鎖
>     - Expect：送 `unlock` 回 `Unlocked: ...`，8 秒後 `Locked`
>     - > Pi 5（2026-11-17）：`lsusb` 看不到 Arduino、沒有 `/dev/ttyACM*`、`dmesg` 沒有插入紀錄
>     - > 使用者（2026-11-17）：Arduino 開關鎖之前 demo 已測過，裝門鎖前都只用 `--sim`
> - [ ] **F2 systemd 服務**（`deploy/`）
>     - Expect：開機自動啟動、`WatchdogSec` 之後再加
> - [ ] **F3 網頁刪除成員在 Pi 上重新 train 的耗時**
>     - Expect：幾秒內完成，網頁不卡
> - [ ] **F4 LINE 真實發送**：`test-line`（使用者確認後）
>     - Expect：手機收到測試訊息，額度正確扣

## 2026-11-17 · 對講機 Pi 5 實機修正

> [!todo]- Group G · 對講機喇叭音量、聆聽免 PIN（smart-door-lock）
> **Branch** `fix/intercom-volume`（PR #3）、`feat/listen-without-pin`（PR #4）· **Status** merged `e642f8c`（兩個 branch 已刪）；Pi 5 已部署 main，pytest 188 passed
> 改到：`smartlock/intercom.py`、`config.py`、`cli.py`、`web/server.py`、`tests/test_intercom.py`、README、`deploy/smartlock.env.example`
> **Why**：Waveshare 卡（`0c76:1203`）在 Linux 上音量刻度錯（dmesg「cval->res is probably wrong」），預設 62% 實際幾乎靜音 → 啟動時自動設 `SPEAKER_VOLUME`（預設 100）。聆聽每次要 PIN 太麻煩 → 比照影像不用 PIN，回覆仍要。
> - [x] **G1 啟動時自動調音量**
>     - Expect：`make_intercom` 之後 `Speaker` 是 100%
>     - > Pi 5（2026-11-17）：先手動設 62%，`make_intercom` 後變 100%（0 dB）
> - [ ] **G2 重新插拔音效卡 / 重開機後直接 `test-audio`**
>     - Expect：不用手動 `amixer` 就聽得到兩個聲道
> - [ ] **G3 手機聆聽不跳 PIN、回覆仍跳 PIN**
>     - Expect：按「聆聽」直接聽得到；按「回覆」跳 PIN 鍵盤，PIN 錯不會播放

## 2026-11-18 · 對講以對話為主

> [!todo]- Group H · v2.11 按住說話 + HTTPS（smart-door-lock）
> **Branch** `feat/https-talk` · **Status** 還沒合併；Mac pytest 204 passed / 4 skipped，Playwright 測過；Pi 5 沒測
> 改到：`smartlock/tls.py`（新）、`intercom.py`、`web/server.py`、`cli.py`、`config.py`、`qr.py`、前端三個檔、`tests/test_tls.py`（新）、`tests/test_intercom.py`、README、指令速查、`deploy/smartlock.env.example`、`.gitignore`
> **Why**：他們要求對講以對話為主 → 手機要能對門口說話；瀏覽器只讓 HTTPS 頁面用麥克風。使用者選 Pi 自簽憑證 + 按住說話（半雙工）。
> - [ ] **H1 Pi 5 第一次啟動產生憑證**：`python app.py run --sim`
>     - Expect：log 有「產生自簽憑證」和指紋；`certs/` 有 crt / key（key 是 600）；QR 與網址是 `https://`；第二次啟動不重產
> - [ ] **H2 iPhone Safari 略過警告**：開 `https://<IP>:5050`
>     - Expect：「顯示詳細資訊 → 瀏覽此網站」後正常；影像、聆聽、紀錄都照常；打 `http://<IP>:5050` 會自動轉過去
> - [ ] **H3 按住說話**：第一次按跳 PIN → 允許麥克風 → 按住講
>     - Expect：門外喇叭聽得到、延遲約 0.5 秒內；放開後自動在聆聽，聽得到門口回答；說話中手機聽不到自己的回音
> - [ ] **H4 iOS 說完之後聆聽音量**
>     - Expect：放開後聆聽的音量和說話前差不多（沒有被切到聽筒）；靜音開關打開也有聲音
> - [ ] **H5 兩支手機**：一支說話時另一支按
>     - Expect：另一支顯示「另一支手機正在說話」；預錄也不能同時播
> - [ ] **H6 LINE 內建瀏覽器**
>     - Expect：打不開或按「說話」跳出說明，照說明改用 Safari 能用
> - [ ] **H7 Android Chrome**（有的話）
>     - Expect：「進階 → 繼續前往」後能按住說話
