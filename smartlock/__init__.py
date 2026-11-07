"""smartlock：Raspberry Pi 人臉 + 手勢雙重驗證門鎖。

模組分工
  config    所有設定（Config dataclass，從環境變數讀取）
  events    事件匯流排：狀態機發出事件，其他模組訂閱
  camera    相機（rpicam-vid / Picamera2 / USB）
  face      人臉偵測（YuNet）與辨識（SFace）、capture、train
  learning  回報誤認：嘗試紀錄、收緊個人門檻、冒充者比對
  gesture   MediaPipe 手勢辨識
  arduino   序列埠門鎖控制（Arduino / FakeArduino）
  door      DoorSystem 狀態機（face → gesture → unlock）
  display   預覽視窗、門口顯示畫面
  qr        控制頁網址與 QR code
  web       Flask 控制頁
  cli       指令列入口（capture / train / run / test-*）
"""
__version__ = '2.8.0'
