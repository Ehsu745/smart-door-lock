---
up: "[[Notes]]"
sticker: lucide//lock
---
## 公開 repo 與私人資訊

`Ehsu745/smart-door-lock` 是 **public**（2026-11-16 起），這個 vault 也在裡面，所以**這裡的筆記都會公開**。

> [!warning] 不進 git 的東西
> - **私人資訊**（Pi 帳號 / IP、LINE 官方帳號 ID、本名、Wi-Fi、資安待辦）：只放 repo 根目錄的 `CLAUDE.local.md`（`.gitignore` 擋著）。vault 裡只寫「見 `CLAUDE.local.md`」。之後會改放在使用者的 private md repo
> - **Journal 日誌 / 週記**：`folio/Journal/` 被 `folio/.gitignore` 擋著，只留在本機；`/cmd-rollover` 只 commit `DEV_STATUS.md`
> - commit 前確認沒有 `CLAUDE.local.md` 列的私人字串

## 本機資料夾與備份
> [!info] 位置
> - `~/GITHUB/smart-door-lock`：改寫過歷史的乾淨版本，**在這份開發**；`user.email` 是 GitHub noreply
> - `~/FCU/CE_2026/AI_smartlock_playground/old_ver/smart-door-lock-old`：舊歷史（含私人資訊），已拿掉 remote，只留著參考；**不要加 remote、不要推**，也不要 `git add .`（它的 `.gitignore` 沒擋 `CLAUDE.local.md`）
> - 改寫前完整備份：bundle 檔（位置見 `CLAUDE.local.md`，含私人資訊，不要上傳）
> - Pi 5 上 v2 之前的舊版完整備份：`~/door_pi_gesture_backup_1020`
> - LINE token 從來沒進過 git（掃過全部歷史）

## Folio 怎麼接在這個 repo
> [!info] 結構
> - vault 在 `folio/`，Obsidian 用 **Open folder as vault** 開這個資料夾
> - skills 的本體在 `folio/LLM/skills/`；repo 根目錄的 `.claude/skills/`、`.agents/skills/` 是 **committed stub**（「讀 `folio/LLM/skills/<名稱>/SKILL.md`」），所以 clone 下來就能用 `/cmd-sync`，不用跑 `folio.py link`
> - 換電腦：clone 後在 `folio/` 跑 `python3 Tools/setup/install_plugins.py`（外掛程式碼與主題）；範本 clone 放 `~/GITHUB/folio`（`/cmd-features` 加功能時用）
> - `folio/Tools/rollover/rollover.py` 改過一行：gitignore 的檔案（Journal）不 commit
