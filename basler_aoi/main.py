from camera.line_camera import BaslerLineScanCamera
from camera.line_camera import BaslerAreaScanCamera
from camera.line_camera import HKAreaCamera
from threads.garbber import LineGrabberThread, FrameGrabberThread
from threads.stitcher import StitchThread
from threads.inference import InferenceThread
from queue import Queue, LifoQueue, PriorityQueue # FIFO, Stack, min-heap(小值先出)
import threading
import time
import os
from datetime import datetime
from web_server import run_server, init_app

from config.config import MODEL_NAME, FRAME_QUEUE_MAX, TARGET_FPS, IMAGE_QUEUE_MAX, HOST, PORT


# 使用 Basler 相機模擬器（如果沒有實體相機可用）
os.environ["PYLON_CAMEMU"] = "1"

project_root = os.path.dirname(os.path.abspath(__file__))
model_path = os.path.join(project_root, "model", MODEL_NAME)

# 本次執行的缺陷存檔路徑（根目錄 defects/ 下再用當下時間建子資料夾）
defects_root = os.path.join(project_root, "defects")
os.makedirs(defects_root, exist_ok=True)
run_timestamp = datetime.now().strftime("%Y%m%d_%H-%M-%S")
run_defect_dir = os.path.join(defects_root, run_timestamp)
os.makedirs(run_defect_dir, exist_ok=True)
print(f"💾 缺陷與 metadata 會寫入: {run_defect_dir}")

# line_queue = Queue(maxsize=LINE_QUEUE_MAX)
frame_queue = Queue(maxsize=FRAME_QUEUE_MAX)
image_queue = Queue(maxsize=IMAGE_QUEUE_MAX)

# 全局變數存儲預測結果
current_prediction = {"class": "待機中", "confidence": 0.0}
prediction_lock = threading.Lock()

# 最新缺陷影像（Base64）
last_defect_image = {"b64": None, "ts": None}
image_lock = threading.Lock()
# 用於通知 SSE 的 Condition
defect_condition = threading.Condition()

# 缺陷存檔與 Demo 模式控制
save_options = {"save_defect": False, "demo_mode": False}
save_lock = threading.Lock()

# 相機開關控制
camera_control = {"on": True}
camera_lock = threading.Lock()

# 全局狀態變數：暫停/運行（使用 Event 控制）
pause_event = threading.Event()
pause_event.set()  # 默認為運行狀態（set = 運行, clear = 暫停）

def main():
    # 初始化並啟動相機, 設定FPS
    camera = BaslerAreaScanCamera(target_fps=TARGET_FPS)
    camera.start()

    # 獲取相機詳細信息
    info = camera.get_camera_info()
    if info:
        print("\n📷 相機信息:")
        for key, value in info.items():
            print(f"   {key}: {value}")

    # 實測實際 FPS
    actual_fps = camera.measure_actual_fps(num_frames=30)
    print(f"🚀 相機實際幀率: {actual_fps:.2f} FPS\n")
    # camera.stop()


    # get image from camera thread
    grabber = FrameGrabberThread(camera, frame_queue, pause_event, camera_control, camera_lock)

    # inference thread
    infer = InferenceThread(
        frame_queue,
        model_path,
        pause_event,
        current_prediction,
        prediction_lock,
        last_defect_image,
        image_lock,
        save_options,
        save_lock,
        run_defect_dir,
    )

    # 初始化 Web 服務器（在啟動推論前初始化，以免遺失早期事件）
    init_app(
        current_prediction,
        prediction_lock,
        frame_queue,
        image_queue,
        FRAME_QUEUE_MAX,
        IMAGE_QUEUE_MAX,
        pause_event,
        last_defect_image,
        image_lock,
        save_options,
        save_lock,
        camera_control,
        camera_lock,
        defect_condition,
        run_defect_dir,
    )

    # thread start
    grabber.start()
    infer.start()

    # 啟動 Web 服務器（在單獨的線程中）
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    print(f"🌐 Web 服務器已啟動，請訪問 http://{HOST}:{PORT}")
    
    try:
        # 保持主線程運行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹️ 正在關閉系統...")
        grabber.stop()
        infer.stop()

if __name__ == "__main__":
    main()