import cv2 # 匯入 OpenCV 套件，用於處理影像與相機畫面
import numpy as np # 匯入 NumPy 套件，用於處理陣列資料（機器學習模型的輸入格式）
import os # 匯入作業系統介面套件，用於處理檔案與資料夾路徑
import time # 匯入時間套件，用於讓程式暫停（例如等待相機暖機）
from datetime import datetime # 匯入日期時間套件，用於記錄辨識到人臉的時間
import threading # 匯入執行緒套件，讓網頁伺服器和人臉辨識可以同時在背景執行
from flask import Flask, render_template, jsonify, request # 匯入 Flask 網頁框架套件，用於建立網頁伺服器與 API

# ==========================================
# 1. 初始化路徑設定 
# ==========================================
# 取得目前這支程式所在的絕對路徑資料夾
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 設定存放人臉照片的資料夾路徑 (例如: /你的路徑/faces)
face_dir = os.path.join(BASE_DIR, 'faces')
# 設定訓練好的模型存檔路徑 (face.yml)
model_file = os.path.join(BASE_DIR, 'face.yml')
# 設定人臉 ID 與名字對應表的存檔路徑 (id_map.txt)
id_map_file = os.path.join(BASE_DIR, 'id_map.txt')

# 載入 OpenCV 內建的人臉特徵分類器（用來「找」畫面中哪裡有臉）
face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
# 建立 LBPH 人臉辨識模型（用來「認」這張臉是誰）
recognizer = cv2.face.LBPHFaceRecognizer_create()
# 建立一個空的字典，用來在記憶體中儲存 ID 與名字的對應關係
id_map = {}

# ==========================================
# 2. Flask Web Server Setup (網頁伺服器設定)
# ==========================================
# 初始化 Flask 應用程式
app = Flask(__name__)
# 建立一個全域變數字典，用來記錄目前辨識到的狀態與網頁傳來的動作
global_state = {"detection": None, "action": None}

# 設定網頁的首頁路由 (當使用者連線到網址根目錄時觸發)
@app.route('/')
def index():
    return render_template('index.html') # 回傳 index.html 網頁畫面

# 設定 API 路由：讓前端網頁來獲取目前的人臉辨識狀態
@app.route('/api/status')
def status():
    # 將 global_state 裡的 detection 狀態轉成 JSON 格式回傳給網頁
    return jsonify({"detection": global_state["detection"]})

# 設定 API 路由：接收前端網頁傳來的動作 (例如按下了開門或拒絕按鈕)
@app.route('/api/action', methods=['POST'])
def action():
    data = request.get_json() # 獲取網頁傳來的 JSON 資料
    action_type = data.get("action") # 取得 action 的值 (例如 "open" 或 "reject")
    # 如果動作是 open 或 reject，就更新 global_state 並回傳成功訊息
    if action_type in ["open", "reject"]:
        global_state["action"] = action_type
        return jsonify({"success": True, "message": f"Action {action_type} recorded."})
    # 如果傳來不認識的動作，回傳失敗訊息
    return jsonify({"success": False, "message": "Invalid action."})

# 定義啟動網頁伺服器的函式
def run_flask():
    # 在 0.0.0.0 的 5050 port 啟動伺服器，允許區域網路內的裝置連線
    app.run(host='0.0.0.0', port=5050, debug=False, use_reloader=False)

# ==========================================
# 3. 載入 ID 映射
# ==========================================
# 定義讀取 ID 對應表的函式
def load_id_map():
    global id_map # 宣告要修改全域變數 id_map
    if os.path.exists(id_map_file): # 如果 id_map.txt 檔案存在
        with open(id_map_file, 'r') as f: # 打開檔案來讀取
            for line in f: # 逐行讀取檔案內容
                parts = line.strip().split(':') # 將每一行去頭尾空白後，用冒號 ':' 切割
                if len(parts) == 3: # 如果切割出來剛好有 3 個部分 (ID:名字:權限類別)
                    num, name, category = parts
                    # 將 ID 轉成整數當作 key，名字與類別存成 value 放入字典
                    id_map[int(num)] = (name, category) 

# ==========================================
# 4. 採集人臉資料
# ==========================================
# 定義採集人臉的函式，需傳入使用者的英文名字 (user_id)
def capture_faces(user_id):
    # 開啟預設相機 (0)，並加上 cv2.CAP_V4L2 參數強制使用 Linux 原生驅動 (相容樹莓派)
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    
    if not cap.isOpened(): # 如果相機沒有成功開啟
        print("\n[錯誤] 無法開啟鏡頭！")
        return # 提早結束函式

    print("\n[INFO] 正在喚醒鏡頭，請稍候 2 秒鐘...")
    time.sleep(2) # 暫停 2 秒鐘，讓樹莓派相機有時間調整對焦與曝光 (暖機)
    
    count = 0 # 初始化照片計數器
    print("\n鏡頭已開啟，請正對鏡頭... (按 'q' 鍵可隨時退出)")

    while True: # 建立無限迴圈，不斷擷取相機畫面
        ret, frame = cap.read() # 讀取一張相機畫面 (ret 為是否成功，frame 為影像資料)
        if not ret: # 如果沒有讀到畫面 (例如還在暖機)
            continue # 忽略這次迴圈，繼續嘗試讀取下一張

        # 將彩色畫面轉換成灰階畫面 (人臉偵測通常用灰階處理，速度較快)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # 在灰階畫面中尋找人臉特徵，回傳人臉的座標矩形
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        # 針對每一張偵測到的人臉，提取座標與長寬 (x, y, w, h)
        for (x, y, w, h) in faces:
            # 在彩色畫面上畫出藍色矩形框 (255, 0, 0)，線條粗細為 2
            cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)
            count += 1 # 計數器加 1
            
            # 設定照片檔名 (例如: yang_1.jpg, yang_2.jpg)
            file_name = f"{user_id}_{count}.jpg"
            file_path = os.path.join(face_dir, file_name) # 組合出完整存檔路徑
            # 將灰階畫面中框出的人臉範圍裁切下來，並存成圖片檔
            cv2.imwrite(file_path, gray[y:y + h, x:x + w])

        # 在螢幕上顯示即時畫面視窗
        cv2.imshow('Capturing Faces - Press Q to exit', frame)

        # 等待 100 毫秒並偵測是否按下 'q' 鍵，或者如果照片已經拍滿 30 張
        if cv2.waitKey(100) & 0xFF == ord('q') or count >= 30:
            break # 退出迴圈，停止拍攝

    cap.release() # 釋放相機資源
    cv2.destroyAllWindows() # 關閉所有 OpenCV 顯示的視窗
    print(f"\n人臉採集完成，共擷取 {count} 張照片。")

# ==========================================
# 5. 訓練模型
# ==========================================
# 定義訓練模型的函式
def train_model():
    recognizer = cv2.face.LBPHFaceRecognizer_create() # 重新建立一個 LBPH 辨識器
    detector = cv2.CascadeClassifier('haarcascade_frontalface_default.xml') # 載入人臉偵測器
    # 找出 faces 資料夾裡面所有副檔名為 jpg, jpeg, png 的照片檔案路徑
    image_paths = [os.path.join(face_dir, f) for f in os.listdir(face_dir) if f.endswith(('.jpg', '.jpeg', '.png'))]
    
    if not image_paths: # 如果沒找到半張照片
        print("\n[INFO] faces 資料夾內沒有找到照片！")
        return # 提早結束函式

    face_samples = [] # 用來存放每一張人臉的影像特徵陣列
    ids = [] # 用來存放每張人臉對應的數字 ID
    label_map = {} # 暫存名字與數字 ID 轉換的字典
    current_id = 0 # 數字 ID 的起始值

    print("\n[INFO] 正在讀取人臉資料並進行模型訓練，請稍候...")

    # 逐一讀取剛剛找出的每一張照片
    for image_path in image_paths:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE) # 以灰階模式讀取圖片
        if img is None: continue # 如果圖片讀取失敗則跳過
        
        filename = os.path.basename(image_path) # 取得檔案名稱 (例如: yang_1.jpg)
        user_name = filename.split('_')[0] # 用 '_' 切割檔名，抓出前半段的名字 (yang)

        # 如果這個名字還沒被給過數字 ID，就發一個新的數字 ID 給他
        if user_name not in label_map:
            label_map[user_name] = current_id
            current_id += 1

        user_numeric_id = label_map[user_name] # 取得這個名字對應的數字 ID

        faces = detector.detectMultiScale(img) # 在這張訓練照片裡再次確認人臉位置
        for (x, y, w, h) in faces:
            face_samples.append(img[y:y+h, x:x+w]) # 把人臉部分裁切下來存入特徵陣列
            ids.append(user_numeric_id) # 存入對應的數字 ID

    if len(face_samples) == 0: # 如果所有的照片都認不出人臉特徵
        print("[錯誤] 照片中無法辨識出清楚的人臉特徵，請嘗試重新採集照片。")
        return

    # 把收集到的所有特徵和 ID 丟給模型進行訓練
    recognizer.train(face_samples, np.array(ids))
    # 訓練完成後，將模型存檔為 face.yml
    recognizer.write(model_file)

    # 開啟 id_map.txt 檔案，把名字與數字 ID 的對應關係寫進去
    with open(id_map_file, 'w') as f:
        for name, num_id in label_map.items():
            f.write(f"{num_id}:{name}:master\n") # 預設權限設定為 master

    print(f"\n[成功] 模型訓練完成！共訓練 {len(face_samples)} 張人臉特徵。")
    print(f"[成功] 模型已儲存至：{model_file}")

# ==========================================
# 6. 人臉辨識與開門邏輯
# ==========================================
# 定義開始人臉辨識的函式
def recognize_faces():
    if not os.path.exists(model_file): # 檢查有沒有已經訓練好的模型檔
        print("Model file 'face.yml' not found. Please train the model first.")
        return

    recognizer.read(model_file) # 讀取訓練好的模型
    load_id_map() # 讀取 ID 與名字的對應表

    # 建立一個背景執行緒來啟動 Flask 網頁伺服器 (這樣才不會卡住相機的運作)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start() # 啟動背景網頁伺服器
    
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2) # 再次開啟樹莓派相機
    print("\n[INFO] Starting camera. Open http://localhost:5050 in your browser to view the App Page.")
    print("\n[INFO] 正在喚醒鏡頭，請稍候 2 秒鐘...")
    time.sleep(2) # 暫停 2 秒鐘，讓相機暖機

    while True: # 無限迴圈抓取畫面進行辨識
        ret, frame = cap.read() # 讀取畫面
        if not ret:
            continue # 如果沒畫面就繼續等

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) # 轉成灰階
        # 在畫面中偵測人臉 (加上參數讓偵測更嚴格，避免誤判)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))

        detected_name = "Unknown" # 預設名字為未知
        detected_category = "Stranger" # 預設類別為陌生人

        for (x, y, w, h) in faces: # 針對畫面上偵測到的每一張臉
            # 把臉丟給模型預測，會回傳預測的 ID (idnum) 和差異度/信心值 (confidence)
            idnum, confidence = recognizer.predict(gray[y:y + h, x:x + w])
            
            # confidence 越低代表越像。通常小於 80 視為辨識成功
            if confidence < 80:
                # 去 id_map 裡面尋找這個 ID 對應的名字跟類別，如果找不到就回傳 Unknown
                user_info = next(((name, cat) for num, (name, cat) in id_map.items() if num == idnum), ("Unknown", "Stranger"))
                detected_name, detected_category = user_info
            else:
                detected_name = "Unknown"
                detected_category = "Stranger"

            label = f"{detected_name} ({detected_category})" # 準備要在畫面上顯示的文字
            # 如果是主人(master)或訪客(guest)，框框畫綠色；如果是陌生人，框框畫紅色
            color = (0, 255, 0) if detected_category in ["master", "guest"] else (0, 0, 255)
            
            # 畫出人臉的矩形框
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            # 在框框上方把名字和類別寫上去
            cv2.putText(frame, label, (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            # 更新全域變數 global_state，讓 Flask 網頁伺服器可以隨時抓到最新的辨識結果
            global_state["detection"] = {
                "name": detected_name,
                "category": detected_category,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S") # 記錄當下的時間
            }

        cv2.imshow('Face Recognition', frame) # 顯示即時辨識畫面

        if cv2.waitKey(5) & 0xFF == ord('q'): # 按下 'q' 鍵可退出辨識迴圈
            break

    cap.release() # 關閉相機
    cv2.destroyAllWindows() # 關閉視窗

# ==========================================
# 7. 主程式選單
# ==========================================
def main():
    while True: # 無限迴圈顯示文字選單
        print("\nFace Recognition System")
        print("1. Capture Faces") # 選項 1: 拍照
        print("2. Train Model") # 選項 2: 訓練模型
        print("3. Recognize Faces") # 選項 3: 開始辨識
        print("4. Exit") # 選項 4: 離開系統
        choice = input("Enter your choice: ") # 等待使用者輸入數字

        if choice == '1':
            user_id = input("Enter User ID: ").strip() # 詢問輸入名字，並去除頭尾多餘空白
            capture_faces(user_id) # 呼叫拍照函式
        elif choice == '2':
            train_model() # 呼叫訓練函式
        elif choice == '3':
            recognize_faces() # 呼叫辨識函式
        elif choice == '4':
            break # 結束迴圈，程式終止
        else:
            print("Invalid choice. Please try again.") # 輸入不符合的選項則提示重來

# Python 程式的進入點 (確保檔案是被直接執行，而不是被別的檔案當作模組匯入)
if __name__ == '__main__':
    if not os.path.exists(face_dir): # 如果存放照片的 faces 資料夾不存在
        os.makedirs(face_dir) # 自動建立資料夾
    main() # 啟動主選單
