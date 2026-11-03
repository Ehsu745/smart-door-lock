#!/usr/bin/env bash
# 安裝智慧門鎖背景服務
#   bash deploy/install_service.sh            # 背景執行，不開視窗（建議）
#   bash deploy/install_service.sh --desktop  # 登入桌面後在 Pi 螢幕顯示畫面
#   bash deploy/install_service.sh --remove   # 移除服務
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
MODE="${1:-}"

if [[ "$MODE" == "--remove" ]]; then
    sudo systemctl disable --now smartlock.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/smartlock.service
    systemctl --user disable --now smartlock.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/smartlock.service"
    sudo systemctl daemon-reload; systemctl --user daemon-reload || true
    echo "已移除 smartlock 服務"; exit 0
fi

[[ -x "$DIR/.venv/bin/python" ]] || { echo "找不到 $DIR/.venv，請先建立虛擬環境"; exit 1; }
if [[ ! -f "$DIR/smartlock.env" ]]; then
    cp "$DIR/deploy/smartlock.env.example" "$DIR/smartlock.env"
    chmod 600 "$DIR/smartlock.env"
    echo "已建立 $DIR/smartlock.env，記得改 CONTROL_PIN"
fi

fill() { sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" "$DIR/deploy/smartlock.service"; }

if [[ "$MODE" == "--desktop" ]]; then
    # 使用者服務：跟著桌面登入啟動，才拿得到螢幕
    mkdir -p "$HOME/.config/systemd/user"
    fill | sed -e '/^User=/d' -e '/^SupplementaryGroups=/d' \
             -e 's|^Environment=SHOW_WINDOW=0|Environment=SHOW_WINDOW=1 DISPLAY=:0|' \
             -e 's|^After=.*|After=graphical-session.target|' -e '/^Wants=/d' \
             -e 's|^WantedBy=.*|WantedBy=default.target|' \
        > "$HOME/.config/systemd/user/smartlock.service"
    sed -i 's|^SHOW_WINDOW=0|SHOW_WINDOW=1|' "$DIR/smartlock.env"
    systemctl --user daemon-reload
    systemctl --user enable --now smartlock.service
    echo "已安裝（桌面模式）。查看紀錄：journalctl --user -u smartlock -f"
else
    fill | sudo tee /etc/systemd/system/smartlock.service > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now smartlock.service
    echo "已安裝（背景模式）。查看紀錄：journalctl -u smartlock -f"
fi
