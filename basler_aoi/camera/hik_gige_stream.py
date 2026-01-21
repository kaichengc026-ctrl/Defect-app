import sys
import time
import ctypes
import numpy as np
import cv2

# 1) 改成你本機 MVS 的 MvImport 路徑
MV_IMPORT_PATH = r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport"
sys.path.append(MV_IMPORT_PATH)

from camera.MvImport.MvCameraControl_class import MvCamera
from camera.MvImport.MvErrorDefine_const import MV_OK
from camera.MvImport.CameraParams_const import (
    MV_GIGE_DEVICE,
    MV_ACCESS_Exclusive,
    MV_TRIGGER_MODE_OFF,
    PixelType_Gvsp_RGB8_Packed,
    PixelType_Gvsp_Mono8,
)

DISPLAY_SCALE = 1.0  # 0.5 可縮小顯示

def assert_ok(ret, msg):
    if ret != MV_OK:
        raise RuntimeError(f"{msg} failed. ret=0x{ret:08x}")

def main():
    cam = MvCamera()

    # 枚舉設備
    device_list = cam.MV_CC_EnumDevices()
    # print(f"找到 {device_list.nDeviceNum} 台相機")
    if device_list.nDeviceNum == 0:
        raise RuntimeError("No camera found. 請確認相機已連上、同網段、且未被其他程式占用。")

    # 這裡直接取第 0 台（你也可以改成依 IP / 序號選擇）
    st_dev = device_list.pDeviceInfo[0]
    assert_ok(cam.MV_CC_CreateHandle(st_dev), "CreateHandle")
    assert_ok(cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0), "OpenDevice")

    # GigE：設定最佳 packet size（減少掉幀/傳輸不穩）
    # 常見做法：先 GetOptimalPacketSize，再寫到 GevSCPSPacketSize。:contentReference[oaicite:2]{index=2}
    if st_dev.nTLayerType == MV_GIGE_DEVICE:
        optimal = cam.MV_CC_GetOptimalPacketSize()
        if optimal > 0:
            cam.MV_CC_SetIntValue("GevSCPSPacketSize", optimal)

    # 關閉觸發 → 連續串流
    assert_ok(cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF), "Set TriggerMode OFF")

    # 開始取流
    assert_ok(cam.MV_CC_StartGrabbing(), "StartGrabbing")

    print("Streaming... 按 q 離開")

    try:
        while True:
            # 取一張（timeout 1000ms）
            ret, frame = cam.MV_CC_GetImageBuffer(1000)
            if ret != MV_OK:
                continue

            # frame 內通常包含：pBufAddr, stFrameInfo(Width/Height/PixelType/FrameLen...)
            w = frame.stFrameInfo.nWidth
            h = frame.stFrameInfo.nHeight
            pixel_type = frame.stFrameInfo.enPixelType
            buf_len = frame.stFrameInfo.nFrameLen

            # 把 SDK buffer 複製成 bytes
            buf = (ctypes.c_ubyte * buf_len).from_address(frame.pBufAddr)
            img = np.frombuffer(buf, dtype=np.uint8)

            # 常見像素格式處理
            if pixel_type == PixelType_Gvsp_Mono8:
                img = img.reshape((h, w))
                bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            elif pixel_type == PixelType_Gvsp_RGB8_Packed:
                img = img.reshape((h, w, 3))
                bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                # 如果你拿到 Bayer / 其他格式：建議先用 MVS 把 PixelFormat 設成 Mono8 或 RGB8 來簡化
                cam.MV_CC_FreeImageBuffer(frame)
                continue

            cam.MV_CC_FreeImageBuffer(frame)

            if DISPLAY_SCALE != 1.0:
                bgr = cv2.resize(bgr, None, fx=DISPLAY_SCALE, fy=DISPLAY_SCALE)

            cv2.imshow("HIK GIGE Stream", bgr)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        cam.MV_CC_StopGrabbing()
        cam.MV_CC_CloseDevice()
        cam.MV_CC_DestroyHandle()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
