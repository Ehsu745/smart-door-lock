"""產生對講機的預設語音（Mac 用內建中文語音「美佳」），存成 16 kHz 16-bit 單聲道 WAV。

  python tools/make_sounds.py                 # 寫到 ./sounds/
  python tools/make_sounds.py ~/somewhere     # 換資料夾
  rsync -av sounds/ pi:door_pi_gesture/sounds/

檔名就是手機上的按鈕文字，開頭的編號用來排序。想用自己的聲音：錄好轉成 16-bit WAV 用同樣的檔名蓋掉即可。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

LINES = ['請稍候，馬上來', '包裹請放門口，謝謝', '現在不方便，請稍後再來']


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'sounds').expanduser()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(LINES, 1):
            aiff, wav = Path(tmp) / f'{i}.aiff', out / f'{i:02d} {text}.wav'
            subprocess.run(['say', '-v', 'Meijia', '-r', '170', '-o', str(aiff), text], check=True)
            subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(wav)], check=True)
            print(wav)


if __name__ == '__main__':
    main()
