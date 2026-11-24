---
sticker: lucide//archive
up: "[[Test Queue]]"
---
Finished groups of [[Test Queue]] (merged, every box ticked), same sections and order.

## 2026-11-18 · 對講以對話為主

> [!success]- Group H · v2.11 按住說話 + HTTPS（smart-door-lock）
> **Branch** `feat/https-talk` · **Status** merged `5e1a57c`（PR #6，branch 已刪）；Mac 212 passed / 4 skipped、Pi 216 passed
> 改到：`smartlock/tls.py`（新）、`intercom.py`、`web/server.py`、`cli.py`、`config.py`、`qr.py`、前端三個檔、`tests/test_tls.py`（新）、`tests/test_intercom.py`、README、指令速查、`deploy/smartlock.env.example`、`.gitignore`
> **Why**：他們要求對講以對話為主 → 手機要能對門口說話；瀏覽器只讓 HTTPS 頁面用麥克風。使用者選 Pi 自簽憑證 + 按住說話（半雙工）。
> - [x] **H1 Pi 5 第一次啟動產生憑證**：`python app.py run --sim`
>     - Expect：log 有「產生自簽憑證」和指紋；`certs/` 有 crt / key（key 是 600）；QR 與網址是 `https://`；第二次啟動不重產
>     - > Pi 5（2026-11-20）：都符合；SAN 含 `.local`、localhost、127.0.0.1、熱點 IP，到 2029-02；區網 http 307 → https，本機 http 200；重啟 md5 相同
> - [x] **H2 iPhone Safari 略過警告**：開 `https://<IP>:5050`
>     - Expect：「顯示詳細資訊 → 瀏覽此網站」後正常；影像、聆聽、紀錄都照常；打 `http://<IP>:5050` 會自動轉過去
>     - > 使用者（2026-11-20）：iPhone Safari 用 `https://` 開，影像、聆聽、說話都能用
> - [x] **H3 按住說話**：第一次按跳 PIN → 允許麥克風 → 按住講
>     - Expect：門外喇叭聽得到、延遲約 0.5 秒內；放開後自動在聆聽，聽得到門口回答；說話中手機聽不到自己的回音
>     - > 使用者（2026-11-20）：第一次「效果很差、按下到能講有不少延遲」→ Pi 5 量到 84°C、`get_throttled=0xe0008`、門鎖程式 358% CPU，風扇接觸不良（0 RPM）。喬好風扇 + 省電（H8）+ 說話預熱（麥克風留 1 分鐘、Pi 沒好前先存著）後：「以上測試都可以」「延遲情況也有變好」
> - [x] **H4 iOS 說完之後聆聽音量**
>     - Expect：放開後聆聽的音量和說話前差不多（沒有被切到聽筒）；靜音開關打開也有聲音
>     - > 使用者（2026-11-20）：可以（麥克風留著的 1 分鐘內也正常）
> - [x] **H5 兩支手機**：一支說話時另一支按
>     - Expect：另一支顯示「另一支手機正在說話」；預錄也不能同時播
>     - > 使用者（2026-11-20）：pass
> - [x] **H6 LINE 內建瀏覽器**
>     - Expect：打不開或按「說話」跳出說明，照說明改用 Safari 能用
>     - > 使用者（2026-11-20）：LINE 裡顯示「無法開啟頁面」（自簽憑證不能略過，網頁的說明也看不到）；「⋯ → 用預設瀏覽器開啟」可以
>     - > 使用者（2026-11-20）：網址加 `?openExternalBrowser=1`（LINE 自動改用 Safari）先不用：LINE 通知沒有附網址，只有用 LINE 掃 QR、在 LINE 點分享的網址才會遇到
> - [x] **H7 Android Chrome**（有的話）
>     - Expect：「進階 → 繼續前往」後能按住說話
>     - > 使用者（2026-11-20）：pass
> - [x] **H8 省電（沒人時少找人臉）**：`IDLE_AFTER` 3 秒、`IDLE_FPS` 2、移動偵測
>     - Expect：沒人時門鎖程式 CPU 明顯下降、溫度不再貼 85°C；走進鏡頭馬上恢復
>     - > Pi 5（2026-11-20，風扇修好後）：358% → 36～48% CPU，82°C → 57°C，風扇 10,200 → 6,000 RPM，`get_throttled` 0xe0008 → 0xe0000；走進鏡頭恢復還沒在門口實測
> - [x] **H9 iPhone 長按不跳選單**
>     - Expect：按住說話 5 秒以上、手指滑到影像上，不會跳「儲存影像」選單
>     - > 使用者（2026-11-20）：原本會跳；影像區關掉 touch-callout、按鈕擋 touchstart 後「可以了」
> - [x] **H10 兩顆喇叭一起響**：`INTERCOM_CHANNEL` 預設改 `both`（他們不喜歡只響一邊）
>     - Expect：按住說話、預錄，門外和室內喇叭都有聲音
>     - > 使用者（2026-11-20）：check
