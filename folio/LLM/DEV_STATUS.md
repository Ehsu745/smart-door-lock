---
up: "[[LLM]]"
aliases: []
sticker: lucide//activity
---
> [!IMPORTANT]
> **LLM Instructions**: Each `/cmd-*` command is an agent skill (`LLM/skills/`); shared rules are in [[LLM_GUIDE]]. `/cmd-sync` keeps this list.

## 🚀 Today's Progress (2026-11-17)
> [!abstract]- 6 items
> - **smart-door-lock-old 搬家**：移到 `AI_smartlock_playground/old_ver/`，刪掉 `.venv`（877M → 4M）
> - **Pi 5 升級 v2.5 → v2.10**：先備份 `~/door_pi_gesture_backup_v2.5_20261117.tgz`，pytest 全過；SFace 模型下載、舊照片登錄 60 張
> - **對講機 Pi 5 實測**：音效卡、錄音、兩聲道播放、手機聆聽 / 回覆都通；回音可接受（[[Test Queue]] E）
> - **喇叭沒聲音找到原因**：USB 卡音量刻度錯，預設 62% ≈ 靜音 → PR #3 啟動時自動設 100%
> - **聆聽免 PIN**：比照影像，回覆仍要 PIN（PR #4）
> - **PR #2、#3、#4 合併**，Pi 5 部署 main，pytest 188 passed（[[Test Queue]] G）

## 📅 Yesterday's Progress (2026-11-16)
> [!abstract]- 4 items
> - **GitHub 公開**：smart-door-lock 以乾淨歷史重建並改成 public，舊資料夾改名 -old、拿掉 remote
> - **PR #1 合併**：face-accuracy（模型比較腳本、可靠度規劃）合回 main
> - **升級成 Folio**：vault 放 folio/、skills 用 committed stub、CLAUDE.md 的進度搬進 vault；PR #2（已於 11-17 合併）
> - **Obsidian 開啟 vault**：12 個外掛啟用，Calendar 固定 1.5.10

---

## ⏳ Ongoing Progress
### 🍓 [PI] Pi 5 實機驗證
> [!todo]- 7 open
> - **v2.6～v2.10 已部署到 Pi 5（11-17），門口實測還沒做**（細項：[[Test Queue]] A～G）：
>     - [ ] **人臉 SFace**：等相機固定在門框；門口光線下本人距離分布、戴 / 不戴眼鏡、Pi 5 速度（Group A）
>     - [ ] **回報誤認**：margin 3 / floor 35 用門口資料驗證（Group B）
>     - [ ] **手勢綁定**：eric、jenny 都還沒綁；Pi 上實測、iPhone / LINE 內建瀏覽器滾輪手感（Group C）
>     - [ ] **check-face / 補拍**：相機裝好就跑（Group D）
>     - [/] **對講機**：硬體通了；剩門口 1 m 收音、LINE 內建瀏覽器、重插後自動音量、聆聽免 PIN 手機實測（Group E、G）
>     - [ ] **正式模式**：Arduino、systemd、網頁刪除成員耗時、LINE 真實發送（Group F）
> - [?] **成員權限**：Pi 上 jenny 是 master、eric 是 guest，問使用者是不是本來就這樣設

---
### 🔩 [HW] 硬體
> [!todo]- 4 open
> - [ ] **Arduino 連不上**：11/04、11/17（Pi 5）`lsusb` 都看不到 UNO → 檢查 USB 線是不是資料線、換孔（音效卡第一個孔也沒反應）
> - [ ] **鎖的種類**：fail-secure / fail-safe、繼電器 NO / NC 還沒確認（[[提高可靠度]]）
> - [ ] **喇叭**：品牌型號與阻抗（4 Ω / 8 Ω）看背面標示（[[對講機]]）
> - [ ] **Pi 4 沒有 USB 鏡頭、沒接音效卡**

---
### 🖥️ [PI4] Pi 4 維護
> [!todo]- 4 open
> - [!] **忘記登入密碼**：`sudo` 不能用，要拔 SD 卡重設，等使用者動手（步驟：[[Pi 4 維護]]）
> - [ ] **加 Wi-Fi**：要 sudo 或在 Pi 桌面本機做；只加設定、不切換現在的連線
> - [?] **`~/.ssh/config` 的 `Host pi4` 改用 mDNS 名稱**：寫死 IP，換網路就連不到；先問使用者
> - [ ] **關機**：也要 sudo，使用者從桌面關

---
### 🧭 [PLAN] 規劃（[[Plan]]）
> [!todo]- 6 open
> - [/] **辨識模型比較 SFace vs ArcFace**：`speed`、`extract` 完成，`report` 還沒跑；暫緩，先做對講機（[[辨識模型比較]]）
> - [ ] **可靠度第一優先**：控制器裝門內、門磁、連續失敗鎖定、用實測資料定門檻（[[提高可靠度]]）
> - [ ] **活體偵測**：先做軟體（防偽模型、眨眼），用攻擊測試數據決定要不要買 ToF / IR（[[提高可靠度]]）
> - [ ] **對講機階段 3：門鈴**（[[對講機]]）
> - [ ] **對講機階段 4：手機對門口講話**：等 HTTPS（Tailscale）
> - [ ] **軟體移動偵測 + 效能量測**：優化規劃第 5 項，改前改後量 CPU 與溫度放進報告

---
### 🧹 [MISC] 雜項
> [!todo]- 3 open
> - [?] **刪 `~/FCU/CE_2026/AI_smartlock_playground/bench_input.tgz` 和 `ref_cloud_gesture_binding/`**：已用完，刪之前問使用者
> - [ ] **PIN 改 6 位**：`python app.py set-pin`（Pi 上還是預設值）
> - [ ] **LINE 改用 push**：設 `LINE_USER_ID`，broadcast 會發給所有好友

---
## 🏁 Last Progress
History: `LLM/PROGRESS_ARCHIVE.md` (feature `archive`), or the vault's git history.
