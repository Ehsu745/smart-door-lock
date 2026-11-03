"""相容入口：python app.py <指令> 等同於 python -m smartlock <指令>。程式本體在 smartlock/ 套件裡。"""
import sys

from smartlock.cli import main

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
