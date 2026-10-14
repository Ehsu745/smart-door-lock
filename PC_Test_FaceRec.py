import cv2
import numpy as np
import os
import time
from datetime import datetime
import threading
from flask import Flask, render_template, jsonify, request

# ==========================================
# 1. 初始化路徑設定 
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
face_dir = os.path.join(BASE_DIR, 'faces')
model_file = os.path.join(BASE_DIR, 'face.yml')
id_map_file = os.path.join(BASE_DIR, 'id_map.txt')

face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
recognizer = cv2.face.LBPHFaceRecognizer_create()
id_map = {}

# ==========================================
# 2. Flask Web Server Setup
# ==========================================
app = Flask(__name__)
global_state = {"detection": None, "action": None}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    return jsonify({"detection": global_state["detection"]})

@app.route('/api/action', methods=['POST'])
def action():
    data = request.get_json()
    action_type = data.get("action")
    if action_type in ["open", "reject"]:
        global_state["action"] = action_type
        return jsonify({"success": True, "message": f"Action {action_type} recorded."})
    return jsonify({"success": False, "message": "Invalid action."})

def run_flask():
    app.run(host='0.0.0.0', port=5050, debug=False, use_reloader=False)

# ==========================================
# 3. 載入 ID 映射
# ==========================================
def load_id_map():
    global id_map
    if os.path.exists(id_map_file):
        with open(id_map_file, 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                if len(parts) == 3:
                    num, name, category = parts
                    id_map[int(num)] = (name, category)

# ==========================================
# 4. 採集人臉資料
# ==========================================
def capture_faces(user_id):
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    
    if not cap.isOpened():
        print("\n[錯誤] 無法開啟鏡頭！")
        return

    print("\n[INFO] 正在喚醒鏡頭，請稍候 2 秒鐘...")
    time.sleep(2)
    
    count = 0
    print("\n鏡頭已開啟，請正對鏡頭... (按 'q' 鍵可隨時退出)")

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        for (x, y, w, h) in faces:
            cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)
            count += 1
            
            file_name = f"{user_id}_{count}.jpg"
            file_path = os.path.join(face_dir, file_name)
            cv2.imwrite(file_path, gray[y:y + h, x:x + w])

        cv2.imshow('Capturing Faces - Press Q to exit', frame)

        if cv2.waitKey(100) & 0xFF == ord('q') or count >= 30:
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n人臉採集完成，共擷取 {count} 張照片。")

# ==========================================
# 5. 訓練模型
# ==========================================
def train_model():
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    detector = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
    image_paths = [os.path.join(face_dir, f) for f in os.listdir(face_dir) if f.endswith(('.jpg', '.jpeg', '.png'))]
    
    if not image_paths:
        print("\n[INFO] faces 資料夾內沒有找到照片！")
        return

    face_samples = []
    ids = []
    label_map = {}
    current_id = 0

    print("\n[INFO] 正在讀取人臉資料並進行模型訓練，請稍候...")

    for image_path in image_paths:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None: continue
        
        filename = os.path.basename(image_path)
        user_name = filename.split('_')[0]

        if user_name not in label_map:
            label_map[user_name] = current_id
            current_id += 1

        user_numeric_id = label_map[user_name]

        faces = detector.detectMultiScale(img)
        for (x, y, w, h) in faces:
            face_samples.append(img[y:y+h, x:x+w])
            ids.append(user_numeric_id)

    if len(face_samples) == 0:
        print("[錯誤] 照片中無法辨識出清楚的人臉特徵，請嘗試重新採集照片。")
        return

    recognizer.train(face_samples, np.array(ids))
    recognizer.write(model_file)

    with open(id_map_file, 'w') as f:
        for name, num_id in label_map.items():
            f.write(f"{num_id}:{name}:master\n")

    print(f"\n[成功] 模型訓練完成！共訓練 {len(face_samples)} 張人臉特徵。")
    print(f"[成功] 模型已儲存至：{model_file}")

# ==========================================
# 6. 人臉辨識與開門邏輯
# ==========================================
def recognize_faces():
    if not os.path.exists(model_file):
        print("Model file 'face.yml' not found. Please train the model first.")
        return

    recognizer.read(model_file)
    load_id_map()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    print("\n[INFO] Starting camera. Open http://localhost:5050 in your browser to view the App Page.")
    print("\n[INFO] 正在喚醒鏡頭，請稍候 2 秒鐘...")
    time.sleep(2)

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))

        detected_name = "Unknown"
        detected_category = "Stranger"

        for (x, y, w, h) in faces:
            idnum, confidence = recognizer.predict(gray[y:y + h, x:x + w])
            
            if confidence < 80:
                user_info = next(((name, cat) for num, (name, cat) in id_map.items() if num == idnum), ("Unknown", "Stranger"))
                detected_name, detected_category = user_info
            else:
                detected_name = "Unknown"
                detected_category = "Stranger"

            label = f"{detected_name} ({detected_category})"
            color = (0, 255, 0) if detected_category in ["master", "guest"] else (0, 0, 255)
            
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(frame, label, (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            global_state["detection"] = {
                "name": detected_name,
                "category": detected_category,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        cv2.imshow('Face Recognition', frame)

        if cv2.waitKey(5) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

# ==========================================
# 7. 主程式選單
# ==========================================
def main():
    while True:
        print("\nFace Recognition System")
        print("1. Capture Faces")
        print("2. Train Model")
        print("3. Recognize Faces")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == '1':
            user_id = input("Enter User ID: ").strip()
            capture_faces(user_id)
        elif choice == '2':
            train_model()
        elif choice == '3':
            recognize_faces()
        elif choice == '4':
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == '__main__':
    if not os.path.exists(face_dir):
        os.makedirs(face_dir)
    main()
