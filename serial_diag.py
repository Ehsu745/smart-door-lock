"""Pi <-> Arduino 序列埠診斷。執行：python3 serial_diag.py（先關掉 app.py 和 Arduino IDE）"""
import glob, grp, os, subprocess, sys, time

print('== 1. pyserial ==')
try:
    import serial
    print('OK', serial.__version__, serial.__file__)
    if not hasattr(serial, 'Serial'):
        sys.exit('裝到錯的套件 "serial"：pip uninstall serial 後改裝 pyserial（sudo apt install python3-serial）')
except ImportError:
    sys.exit('沒有 pyserial：sudo apt install python3-serial')

print('\n== 2. 序列埠 ==')
ports = sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))
print(ports or '找不到！換一條可傳資料的 USB 線，並確認 Arduino 的 ON 燈有亮')
print(glob.glob('/dev/serial/by-id/*'))
print(subprocess.run(['lsusb'], capture_output=True, text=True).stdout)
if not ports:
    sys.exit()

print('== 3. 權限 ==')
groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
print('dialout 群組：', 'dialout' in groups or '不在！sudo usermod -aG dialout $USER 後重新登入')

print('\n== 4. 佔用 ==')
for p in ports:
    r = subprocess.run(['fuser', p], capture_output=True, text=True)
    print(p, '被 PID 佔用：' + r.stdout.strip() if r.stdout.strip() else '未被佔用')

print('\n== 5. 逐一 baud 測試 ==')
for p in ports:
    for baud in (9600, 115200, 57600, 19200, 38400):
        try:
            s = serial.Serial(p, baud, timeout=0.3)
        except serial.SerialException as e:
            print(p, '開啟失敗：', e); break
        time.sleep(2.5)                      # 等 auto-reset 完成
        boot = s.read(200)
        s.write(b'LOCK\n'); s.flush()
        time.sleep(0.5)
        reply = s.read(200)
        s.close()
        print(f'{p} @ {baud:>6}: 開機訊息={boot!r}  LOCK 回覆={reply!r}')
        if b'LOCKED' in reply or b'READY' in boot:
            print(f'\n>>> 正確設定：PORT={p!r}, BAUD={baud}（並確認這就是新版 door_lock.ino）')
            sys.exit()
print('\n全部 baud 都沒有正確回覆：Arduino 上大概不是新版 door_lock.ino，請用 IDE 重新上傳')
