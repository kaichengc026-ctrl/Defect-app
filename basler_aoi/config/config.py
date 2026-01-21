HOST = '127.0.0.1'
PORT = 5000



# model 版本, 放入 ./model 資料夾下, 支援 ultralytics YOLO 格式的模型
MODEL_NAME = "best_ccdft_260107_1.pt"

# 相機目標幀數
TARGET_FPS = 2


# 隊列最大長度設定
FRAME_QUEUE_MAX = 3000
IMAGE_QUEUE_MAX = 8

# 虛擬相機使用
USE_CAMERA_EMULATOR = True
CAMERA_EMULATOR_DIR = r"C:\Users\kenny\Desktop\Defect_APP\datasets\ccd_all\demo\\"

