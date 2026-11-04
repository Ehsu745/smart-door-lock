"""只預覽手機控制頁（示範模式），不需要相機、Arduino 或門鎖程式。

  python tools/preview_web.py            # http://localhost:8800
  python tools/preview_web.py 9000       # 換埠號

沒有後端，頁面連不到 /api/status 會自動進入示範模式（示範 PIN 1234）。
同一個 Wi-Fi 的手機可以用印出來的區網網址開。每次回應都加 no-cache，改完檔案直接重新整理即可。
"""
import functools
import http.server
import socket
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / 'smartlock' / 'web'
PAGE = '/templates/index.html'


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        if self.path in ('/', ''):
            self.send_response(302)
            self.send_header('Location', PAGE)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/'):          # 示範模式每秒會試一次 /api/status，不印
            super().log_message(fmt, *args)


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), functools.partial(Handler, directory=str(WEB)))
    print(f'手機控制頁預覽（示範模式，PIN 1234）\n  電腦：http://localhost:{port}\n  手機：http://{lan_ip()}:{port}\nCtrl+C 結束', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
