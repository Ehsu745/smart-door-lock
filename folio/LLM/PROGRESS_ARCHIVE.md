---
up: "[[LLM]]"
sticker: lucide//archive
---
Finished work moved out of [[DEV_STATUS]] (`/cmd-notes-sync` adds to it). Read only for the weekly report history or when asked.

## 2026-11-14～16 · GitHub 公開
- **歷史改寫**：`git filter-repo` 清掉私人資訊、作者 email 改 GitHub noreply；改寫前完整備份成 bundle（位置見 `CLAUDE.local.md`）
- **重建 repo**：刪掉舊 `Ehsu745/smart-door-lock`，推乾淨版本後改 public；舊資料夾 `smart-door-lock-old` 拿掉 remote
- **PR #1**：`face-accuracy` 合回 main（`tools/bench_face_models.py`、模型比較與可靠度規劃）
- **Pi 4 同步 v2.10**：pytest 183 passed（含 Linux 才跑的序列埠測試）

## v2.10 · 對講機階段 1、2（2026-11-14）
- **聽門口、播預錄語音**：`intercom.py`（arecord / aplay，不加 Python 套件），網頁聆聽 / 回覆，`test-audio`、`tools/make_sounds.py`；測試 156 → 183。細節：[[對講機實作]]

## v2.9 · 人臉檢查與分次補拍（2026-11-07）
- **`capture 名字 [張數]`、`check-face [秒數]`**：不調門檻，改成換地方架設就檢查、不穩就補拍。細節：[[人臉辨識]]

## v2.8 · 手勢綁定（2026-11-07）
- **每個主人綁定一個開鎖手勢**：`bindings.py`、比錯累計冷卻、不洩漏規格、手機 UI（PIN → 滾輪）；測試 136 → 151。細節：[[手勢綁定]]

## v2.7 · 回報誤認（2026-11-07）
- **時間軸回報「這不是 某某？」**：個人門檻 + 冒充者比對兩道防線，可取消、可恢復預設。細節：[[回報誤認]]

## v2.6 · 人臉辨識改版 LBPH → SFace（2026-11-05）
- **YuNet + SFace**：LFW 5,524 位陌生人單幀 0 誤認（95% 上限約 0.054%）；`train` 只轉特徵、舊資料免重拍。細節：[[人臉辨識]]、repo `docs/04_人臉辨識改版_v2.6.md`

## v2.5 以前（～2026-11-04）
- **優化規劃 1～4**：模組化 + 狀態機測試、systemd 服務（`deploy/`）、哨兵錄影 + SQLite + 控制頁、LINE 文字通知
- **手機單頁 App**：門鎖 / 成員 / 紀錄 / 設定、PIN 雜湊與網頁變更、網頁變更主人 / 訪客、網頁刪除成員（v2.4）、串流連線數修正（v2.2.1）、依手機尺寸排版；2026-11-04 改版細節：[[手機頁]]
- **2026-11-04 Pi 實測**（v2.5，`run --sim`）通過：見 [[Plan]] Phase 0
