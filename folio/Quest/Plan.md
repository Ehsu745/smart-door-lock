---
sticker: lucide//milestone
up: "[[Quest]]"
---
The project's phases, for work built in planned steps and checked on its target (the phone, a board, staging). `/cmd-phase` records a phase that passed and opens the next one; each phase's checks are a group in [[Test Queue]].

目標裝置：**Pi 5（門口）**＋手機（iPhone、LINE 內建瀏覽器）。開發流程：本地開發 → pytest → 手機 UI 有改時先給單檔 HTML 試 → 進 main → 上 Pi 驗收。

## Current phase

### Phase 1: v2.6～v2.10 上 Pi 5 驗收 (2026-11-16)

> [!NOTE] Status: in progress
> v2.6（SFace）到 v2.10（對講機階段 1、2）都已進 main，但只在 Mac 和 Pi 4 跑過，還沒在 Pi 5 門口實測。部署 v2.10 到 Pi 5，跑完 [[Test Queue]] A～F。
> **Passed means**：Pi 5 `--sim` 模式下，本人人臉 + 綁定手勢判定開門、陌生人與訪客不開；手機能聽門口聲音、播預錄語音。接 Arduino 正式模式等裝門鎖時再測（Arduino 開關鎖之前 demo 已測過）。

## Passed

### Phase 0: v2.5 上 Pi 實測（2026-11-04）✅ passed on Pi 5 2026-11-04
> [!done] `run --sim` 通過
> 舊資料不用重訓、手機控制頁、網頁變更權限、LINE 測試訊息 / 訪客通知（含 5 分鐘冷卻）/ 陌生人錄影與通知、正常結束先上鎖。Arduino 正式模式那天沒測（`lsusb` 看不到 UNO）。

## Later
- **Phase 2: 可靠度第一優先**：控制器裝門內、門磁（`door_opened` / `door_forced` / `door_left_open`）、連續失敗鎖定、用實測資料定門檻（[[提高可靠度]]）
- **Phase 3: 辨識模型比較**：跑完 `report`，依判斷規則決定換不換 ArcFace mbf（[[辨識模型比較]]）
- **Phase 4: 對講機階段 3 門鈴**：按鈕 → `doorbell` 事件 → LINE + 室內喇叭（[[對講機]]）
- **Phase 5: 活體偵測（軟體）**：防偽模型 / 眨眼，攻擊測試集前後各跑一輪（[[提高可靠度]]）
- **Phase 6: 軟體移動偵測 + 效能量測**：低解析度幀差，沒動靜跳過辨識；量 CPU 與溫度
- **Phase 7: HTTPS + 對講機階段 4**：Tailscale，手機對門口講話；PIN 不再明文
