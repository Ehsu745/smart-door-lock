"""把手機控制頁合成一個獨立的 .html（CSS、JS 全部內嵌），不用伺服器就能開，給手機上試版面用。

  python tools/build_single_html.py                       # 輸出到 dist/smartlock_demo.html
  python tools/build_single_html.py ~/Desktop/demo.html   # 指定輸出位置

連不到 /api/status 會自動進入示範模式（示範 PIN 1234），所有操作只改瀏覽器裡的假資料，重新整理就還原。
字型從 Google Fonts 載入；沒網路時會用系統字型，版面不受影響。
"""
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'smartlock' / 'web'


def inline(html: str) -> str:
    def css(_):
        return '<style>\n' + (WEB / 'static' / 'app.css').read_text(encoding='utf-8') + '\n</style>'

    def js(m):
        code = (WEB / 'static' / m.group(1)).read_text(encoding='utf-8')
        return '<script>\n' + code.replace('</script', '<\\/script') + '\n</script>'

    html, n_css = re.subn(r'<link rel="stylesheet" href="\.\./static/app\.css">', css, html)
    html, n_js = re.subn(r'<script src="\.\./static/([\w.]+\.js)"></script>', js, html)
    if n_css != 1 or n_js != 2:
        raise SystemExit(f'index.html 的資源標籤變了（css {n_css}、js {n_js}），請更新這支工具')
    if re.search(r'(src|href)="\.\./static/', html):
        raise SystemExit('還有沒內嵌到的 ../static/ 資源')
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    return html.replace('<head>', f'<head>\n<!-- 單檔示範版，由 tools/build_single_html.py 產生於 {stamp} -->', 1)


def main():
    out = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else ROOT / 'dist' / 'smartlock_demo.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(inline((WEB / 'templates' / 'index.html').read_text(encoding='utf-8')), encoding='utf-8')
    print(f'已產生 {out}（{out.stat().st_size / 1024:.0f} KB）')


if __name__ == '__main__':
    main()
