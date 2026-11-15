---
sticker: lucide//list-checks
up: "[[Quest]]"
---
Test checklists, one group per branch or batch of work, in the order it was done. Groups that are merged with every box ticked move to [[Test Queue Archive]] (their Index row stays). Rules: skill `test-queue`.

## Index

| Group | Topic | Repo | Branch | Status |
| :--- | :--- | :--- | :--- | :--- |
| A | v2.6 人臉 SFace | smart-door-lock | main | merged，Pi 5 待測 |
| B | v2.7 回報誤認 | smart-door-lock | main | merged，Pi 5 待測 |
| C | v2.8 手勢綁定 | smart-door-lock | main | merged，Pi 5 待測 |
| D | v2.9 check-face / 補拍 | smart-door-lock | main | merged，Pi 4 部分測過 |
| E | v2.10 對講機階段 1、2 | smart-door-lock | main | merged，Pi 4 部分測過 |
| F | 正式模式與部署 | smart-door-lock | main | Pi 5 待測 |

## 2026-11-16 · Pi 5 驗收（[[Plan]] Phase 1）

> [!todo]- Group A · v2.6 人臉 SFace（smart-door-lock）
> **Branch** `main` · **Status** merged（`v2.6-sface` 已合併）；Mac 與 LFW 測過，Pi 5 沒測
> **Why**：學弟 demo 時陌生人被認成 eric；LBPH 門檻 70 誤認 8.6%，SFace 門檻 50 為 0 / 5,524。要確認門口實際條件下本人也認得到。
> - [ ] **A1 速度**：Pi 5 上 identify / 手勢每幀耗時
>     - Expect：與 Pi 4（identify 126 ms、手勢 117 ms）同級或更快，不降頻
> - [ ] **A2 門口本人距離分布**：門口光線下 `check-face`
>     - Expect：中位數 < 40，門檻 50 通過率高、連續 8 幀達得到
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
> **Branch** `main` · **Status** merged；Mac 模擬伺服器使用者實測通過（2026-11-14）；Pi 4 同步 v2.10，pytest 183 passed
> **Why**：學弟實驗室教授指定的 USB 音效卡 + 兩顆喇叭；細節見 [[對講機]]。
> - [ ] **E1 `python app.py test-audio` 找得到卡**
>     - Expect：列出 USB PnP 卡、錄 5 秒有 RMS、左右聲道各播一次
>     - > Pi 4（2026-11-15）：找不到音效卡（還沒接）
> - [ ] **E2 PipeWire 會不會占住 plughw**
>     - Expect：被占用時 `AUDIO_DEVICE=default` 可用
> - [ ] **E3 USB 卡 1 聲道錄音、門口 1 m 收音**
>     - Expect：手機聽得清楚
> - [ ] **E4 喇叭最大聲時欠壓**：`vcgencmd get_throttled`
>     - Expect：`0x0`
> - [ ] **E5 iPhone / LINE 內建瀏覽器**：fetch 串流 + Web Audio 聆聽、回覆播放
>     - Expect：聽得到、延遲約 0.3 秒；靜音開關打開也有聲音

> [!todo]- Group F · 正式模式與部署（smart-door-lock）
> **Branch** `main` · **Status** 還沒測
> **Why**：11/04 Pi 實測只跑 `--sim`，正式模式（會真的開鎖）一直沒驗證；啟動前先問使用者。
> - [ ] **F1 Arduino 正式模式**：`lsusb` 看得到 UNO、`test-arduino` 開 / 關鎖
>     - Expect：送 `unlock` 回 `Unlocked: ...`，8 秒後 `Locked`
> - [ ] **F2 systemd 服務**（`deploy/`）
>     - Expect：開機自動啟動、`WatchdogSec` 之後再加
> - [ ] **F3 網頁刪除成員在 Pi 上重新 train 的耗時**
>     - Expect：幾秒內完成，網頁不卡
> - [ ] **F4 LINE 真實發送**：`test-line`（使用者確認後）
>     - Expect：手機收到測試訊息，額度正確扣
