---
up: "[[LLM]]"
aliases: []
sticker: lucide//activity
---
> [!IMPORTANT]
> **LLM Instructions**: Each `/cmd-*` command is an agent skill (`LLM/skills/`); shared rules are in [[LLM_GUIDE]]. `/cmd-sync` keeps this list.

## 🚀 Today's Progress (2026-11-20)

## 📅 Yesterday's Progress (2026-11-18)
> [!abstract]- 4 items
> - **Arduino 暫緩到裝門鎖**：偵測不到是 USB 那層；demo 已測過開關鎖，裝鎖前一律 `--sim`
> - **按住說話 + HTTPS（v2.11）**：Pi 自簽憑證、同埠 http 轉 https、半雙工按住說話；Mac 測過，commit 並開 PR #6（draft）
> - **rollover 自動建日記**：`rollover.py` 找不到日記或還是原始範本時，自己照範本規則建立
> - **rollover 11-17**：完成

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
>     - [ ] **正式模式**：systemd、網頁刪除成員耗時、LINE 真實發送（Group F）；Arduino 已通（F1），裝門鎖前照用 `--sim`
>     - [/] **按住說話 + HTTPS（v2.11，`feat/https-talk`，PR #6 draft）**：Pi 5 + iPhone 通過 H1～H4、H8、H9（11-20）；H1～H10 全部通過，可以合併；11-20 的修改已推上 PR #6（Group H）
> - [x] **成員權限**：Pi 上 jenny 是 master、eric 是 guest，使用者確認維持不變（11-17）

---
### 🔩 [HW] 硬體
> [!todo]- 3 open
> - [x] **Arduino 連上了（11-20）**：重新插好後 Pi 5 認得 UNO（`/dev/ttyACM0`、9600），`serial_diag.py`、`test-arduino` 都通過；步驟寫進 `指令速查.md`。裝門鎖前照用 `--sim`
> - [x] **Pi 5 風扇接觸不良（11-20）**：系統要全速但 0 RPM、84°C 降頻 → 喬好排線約 10,000 RPM
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
> - [>] **對講機階段 4：手機對門口講話**：v2.11 做完（自簽憑證，不用 Tailscale），等 Pi 5 測試（[[Test Queue]] H）
> - [/] **軟體移動偵測 + 效能量測**：省電模式做完（`feat/https-talk`）：沒人時 358% → 約 40% CPU、82°C → 57°C；門口有人走動時的恢復速度還沒實測

---
### 🧹 [MISC] 雜項
> [!todo]- 3 open
> - [?] **刪 `~/FCU/CE_2026/AI_smartlock_playground/bench_input.tgz` 和 `ref_cloud_gesture_binding/`**：已用完，刪之前問使用者
> - [ ] **PIN 改 6 位**：`python app.py set-pin`（Pi 上還是預設值）
> - [ ] **LINE 改用 push**：設 `LINE_USER_ID`，broadcast 會發給所有好友

---
## 🏁 Last Progress
History: `LLM/PROGRESS_ARCHIVE.md` (feature `archive`), or the vault's git history.
