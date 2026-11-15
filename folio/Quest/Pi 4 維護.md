---
up: "[[Quest]]"
sticker: lucide//cpu
---
使用者自己的 Pi 4（`ssh pi4`），用來先跑測試、量速度。帳號、hostname、IP、Wi-Fi 資料見 `CLAUDE.local.md`（不進 git）。

## 現況（2026-11-15）
> [!info] 狀態
> - Trixie 64-bit，已同步 v2.10（rsync `smartlock/ app.py tests/ tools/ requirements*.txt`，資料不動）：pytest 183 passed
> - identify 126 ms、手勢 117 ms、約 65°C 沒降頻（v2.9 時量）
> - 沒有 USB 鏡頭、沒接音效卡（`test-audio` 找不到卡）；成員只有 `me`（master）

## 待辦
> [!warning]- 4 open
> - [!] **忘記登入密碼**（等使用者動手）：SSH 金鑰登得進去，但 `sudo` 要密碼，所以加 Wi-Fi、關機都做不到。重設：
>     - SD 卡 `cmdline.txt` 行尾加 ` init=/bin/sh` → 開機
>     - `mount -o remount,rw /` → `passwd <使用者>` → `sync`
>     - 拿掉 `init=/bin/sh` 再開機
> - [ ] **加 Wi-Fi**：`nmcli connection add type wifi …` 需要 sudo 或在 Pi 桌面本機執行；只加設定、不要切換現在的連線（會斷 SSH）
> - [?] **`~/.ssh/config` 的 `Host pi4` 改用 mDNS 名稱**：現在寫死 IP，換網路就連不到；先問使用者
> - [ ] **關機**：`systemctl poweroff` 也要 sudo，使用者從桌面關
