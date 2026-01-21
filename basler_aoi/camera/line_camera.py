from pypylon import pylon
import numpy as np
import time
from config.config import USE_CAMERA_EMULATOR, CAMERA_EMULATOR_DIR

class BaslerLineScanCamera:
    def __init__(self, serial=None, exposure_time=5000, gain=0):
        self.serial = serial
        self.exposure_time = exposure_time
        self.gain = gain
        self.camera = None
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = pylon.PixelType_Mono8  # 灰階
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

    def start(self):
        # 選擇相機
        if self.serial:
            info = [c for c in pylon.TlFactory.GetInstance().EnumerateDevices() if c.GetSerialNumber() == self.serial]
        else:
            info = pylon.TlFactory.GetInstance().EnumerateDevices()

        if len(info) == 0:
            raise RuntimeError("No Basler camera found!")
        for i in info:
            print(f"Using Basler camera: {i.GetModelName()} (S/N: {i.GetSerialNumber()})")

        self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(info[0]))
        self.camera.Open()

        # 線掃基本參數
        nodemap = self.camera.GetNodeMap()
        # if nodemap.GetNode("LineScanMode") is not None:
        #     self.camera.LineScanMode.SetValue("Continuous")  # 或 "Triggered"

        self.camera.ExposureTime.SetValue(self.exposure_time)  # 微秒
        self.camera.Gain.SetValue(self.gain)

        # 觸發設定，如果需要外部觸發
        # self.camera.TriggerSelector.SetValue("FrameStart")
        # self.camera.TriggerMode.SetValue("On")
        # self.camera.TriggerSource.SetValue("Line0")  # 外部 Trigger

        self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def get_line(self):
        """
        return: np.ndarray shape=(width,)
        """
        if not self.camera or not self.camera.IsGrabbing():
            return None

        grabResult = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
        if grabResult.GrabSucceeded():
            img = self.converter.Convert(grabResult)
            img_array = img.GetArray()
            grabResult.Release()
            return img_array  # shape = (H=1, W) 或 (W,)
        else:
            grabResult.Release()
            return None

    def stop(self):
        if self.camera and self.camera.IsGrabbing():
            self.camera.StopGrabbing()
        if self.camera and self.camera.IsOpen():
            self.camera.Close()


class BaslerAreaScanCamera:
    def __init__(self, serial=None, exposure_us=5000, gain=0, color=False, target_fps=30):
        self.serial = serial
        self.exposure_us = exposure_us
        self.gain = gain
        self.color = color
        self.target_fps = target_fps
        self.camera = None
        self.converter = pylon.ImageFormatConverter()

        # 轉出 numpy 的格式：彩色(BGR8) 或 灰階(Mono8)
        if self.color:
            self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        else:
            self.converter.OutputPixelFormat = pylon.PixelType_Mono8
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

    def start(self):
        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler camera found.")

        if self.serial:
            matched = [d for d in devices if d.GetSerialNumber() == self.serial]
            if not matched:
                raise RuntimeError(f"Basler camera with serial {self.serial} not found.")
            device = matched[0]
        else:
            device = devices[0]

        self.camera = pylon.InstantCamera(factory.CreateDevice(device))
        self.camera.Open()

        # 測試讀取檔案影像
        print("Found:", len(devices))
        for d in devices:
            print(d.GetModelName(), d.GetSerialNumber())

        self.set_fps(self.target_fps)
        

        if USE_CAMERA_EMULATOR:
            print("⚙️ Using Basler Camera Emulator mode.")
            # 2) 關閉測試圖 + 啟用檔案影像 + 指定檔案/資料夾
            self.camera.TestImageSelector.Value = "Off"
            self.camera.ImageFileMode.Value = "On"

            # ⚠️ 這個路徑一定要是「目前這台機器」可讀到的路徑
            # self.camera.ImageFilename.Value = r"C:\Users\kenny\Desktop\Defect_APP\datasets\ccd251231\val\images"   # Linux 例子
            self.camera.ImageFilename.Value = CAMERA_EMULATOR_DIR       # Windows 例子

        # self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        # grab = self.camera.RetrieveResult(2000, pylon.TimeoutHandling_ThrowException)
        # img = grab.Array
        # print("img:", img.shape, img.dtype)


        # 常用參數（不同機型可能支援項目略有差異）
        # if self.camera.ExposureTime.IsWritable():
        #     self.camera.ExposureTime.SetValue(self.exposure_us)
        # if self.camera.Gain.IsWritable():
        #     self.camera.Gain.SetValue(self.gain)
        # 設定基本參數
        try:
            self.camera.ExposureTime.SetValue(self.exposure_us)
        except Exception as e:
            print(f"Warning: Could not set ExposureTime: {e}")

        try:
            self.camera.Gain.SetValue(self.gain)
        except Exception as e:
            print(f"Warning: Could not set Gain: {e}")

        nodemap = self.camera.GetNodeMap()
        acq_node = nodemap.GetNode("AcquisitionMode")
        if acq_node:
            self.camera.AcquisitionMode.SetValue("Continuous")
        trig_node = nodemap.GetNode("TriggerMode")
        if trig_node:
            self.camera.TriggerMode.SetValue("Off")


        # 降低延遲：只保留最新影像（避免 buffer 越堆越延遲）
        self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def get_frame(self, timeout_ms=5000) -> np.ndarray | None:
        if not self.camera or not self.camera.IsGrabbing():
            print("[WARN] Camera not grabbing.")
            return None

        try:
            grab = self.camera.RetrieveResult(timeout_ms, pylon.TimeoutHandling_ThrowException)
        except Exception as e:
            print(f"[ERROR] RetrieveResult failed: {e}")
            return None
        if grab is None:
            return None

        try:
            if not grab.GrabSucceeded():
                return None

            img = self.converter.Convert(grab)
            arr = img.GetArray()  # Mono: (H,W) / Color(BGR): (H,W,3)
            return arr
        finally:
            grab.Release()

    def set_fps(self, target_fps):
        """設定相機目標幀率"""
        try:
            # 檢查是否支援 AcquisitionFrameRate
            if self.camera.GetNodeMap().GetNode("AcquisitionFrameRate") is not None:
                self.camera.AcquisitionFrameRate.SetValue(target_fps)
                actual_fps = self.camera.AcquisitionFrameRate.GetValue()
                print(f"✅ FPS 設定成功: 目標={target_fps}, 實際={actual_fps:.2f}")
            else:
                print("⚠️ 相機不支援 AcquisitionFrameRate 參數")
        except Exception as e:
            print(f"❌ 設定 FPS 失敗: {e}")
    

    def get_camera_info(self):
        """獲取相機的詳細信息，包括當前 FPS"""
        try:
            info = {
                "model": self.camera.GetDeviceInfo().GetModelName(),
                "serial": self.camera.GetDeviceInfo().GetSerialNumber(),
                # "firmware": self.camera.GetDeviceInfo().GetDeviceFirmwareVersion(),
            }

            # 嘗試獲取當前 FPS
            try:
                if self.camera.GetNodeMap().GetNode("AcquisitionFrameRate") is not None:
                    info["current_fps"] = self.camera.AcquisitionFrameRate.GetValue()
                    info["fps_min"] = self.camera.AcquisitionFrameRate.GetMin()
                    info["fps_max"] = self.camera.AcquisitionFrameRate.GetMax()
            except:
                info["current_fps"] = "Not available"

            # 獲取曝光時間
            try:
                if self.camera.GetNodeMap().GetNode("ExposureTime") is not None:
                    info["exposure_us"] = self.camera.ExposureTime.GetValue()
            except:
                pass

            return info
        except Exception as e:
            print(f"獲取相機信息失敗: {e}")
            return None

    def measure_actual_fps(self, num_frames=100):
        """實測相機的實際 FPS"""
        print(f"📊 正在測量實際 FPS (採樣 {num_frames} 幀)...")
        
        start_time = time.time()
        frame_count = 0

        for _ in range(num_frames):
            frame = self.get_frame(timeout_ms=1000)
            if frame is not None:
                frame_count += 1

        elapsed_time = time.time() - start_time
        actual_fps = frame_count / elapsed_time if elapsed_time > 0 else 0

        print(f"✅ 實測結果: {actual_fps:.2f} FPS (共取得 {frame_count}/{num_frames} 幀)")
        return actual_fps
    def stop(self):
        if self.camera:
            if self.camera.IsGrabbing():
                self.camera.StopGrabbing()
            if self.camera.IsOpen():
                self.camera.Close()


class HKAreaCamera:
    """海康（Hikvision）面掃相機適配器
    實作註記：
    - 使用 `MvImport`（MvCamera wrapper）進行設備枚舉、開啟、取流、參數設置。
    - 若某項功能（例如精確的節點名稱）在當前機型/SDK 無法設定，會以警告訊息跳過。
    """

    def __init__(self, serial=None, exposure_us=5000, gain=0, color=False, target_fps=30):
        self.serial = serial
        self.exposure_us = exposure_us
        self.gain = gain
        self.color = color
        self.target_fps = target_fps

        # MvImport 的相機物件
        try:
            from camera.MvImport.MvCameraControl_class import MvCamera
            from camera.MvImport.CameraParams_header import MV_CC_DEVICE_INFO_LIST, MV_FRAME_OUT, MV_FRAME_OUT_INFO_EX, MV_CC_DEVICE_INFO, MV_GrabStrategy_LatestImagesOnly
            from camera.MvImport.CameraParams_const import MV_GIGE_DEVICE, MV_USB_DEVICE
            from camera.MvImport.PixelType_header import PixelType_Gvsp_Mono8, PixelType_Gvsp_BGR8_Packed
        except Exception as e:

            raise RuntimeError(f"Cannot import MvImport SDK: {e}")

        self.MvCamera = MvCamera
        self.MV_CC_DEVICE_INFO_LIST = MV_CC_DEVICE_INFO_LIST
        self.MV_FRAME_OUT = MV_FRAME_OUT
        self.MV_FRAME_OUT_INFO_EX = MV_FRAME_OUT_INFO_EX
        self.MV_CC_DEVICE_INFO = MV_CC_DEVICE_INFO
        self.MV_GIGE_DEVICE = MV_GIGE_DEVICE
        self.MV_USB_DEVICE = MV_USB_DEVICE
        self.MV_GrabStrategy_LatestImagesOnly = MV_GrabStrategy_LatestImagesOnly
        self.PixelType_Mono8 = PixelType_Gvsp_Mono8
        self.PixelType_BGR8 = PixelType_Gvsp_BGR8_Packed

        self.device_info = None
        self.camera = None

    def start(self):
        """列舉 & 開啟海康相機，設定曝光、增益、FPS，並開始取流"""
        # 枚舉設備
        dev_list = self.MV_CC_DEVICE_INFO_LIST()
        print(dev_list)
        # 同時枚舉 GigE 與 USB 設備
        nType = int(self.MV_GIGE_DEVICE | self.MV_USB_DEVICE)
        try:
            # 優先使用類別的 static method；若不存在則嘗試 module-level / DLL 呼叫，並提供診斷訊息
            if hasattr(self.MvCamera, "MV_CC_EnumDevices"):
                res = self.MvCamera.MV_CC_EnumDevices(nType, dev_list)
            else:
                # 嘗試從模組匯入
                try:
                    from camera.MvImport import MvCameraControl_class as mvmod
                    if hasattr(mvmod, "MV_CC_EnumDevices"):
                        res = mvmod.MV_CC_EnumDevices(nType, dev_list)
                    else:
                        # 嘗試直接呼叫 DLL 裡的符號
                        from ctypes import c_uint, byref
                        if hasattr(mvmod, "MvCamCtrldll") and hasattr(mvmod.MvCamCtrldll, "MV_CC_EnumDevices"):
                            res = mvmod.MvCamCtrldll.MV_CC_EnumDevices(c_uint(nType), byref(dev_list))
                        else:
                            raise AttributeError("MV_CC_EnumDevices not found on class, module, or DLL")
                except Exception as ie:
                    raise RuntimeError(f"MV_CC_EnumDevices lookup failed: {ie}")
            # 檢查回傳碼：0 表示成功（依 SDK 規範）
            if isinstance(res, int) and res != 0:
                raise RuntimeError(f"MV_CC_EnumDevices returned error code: {res}")
        except Exception as e:
            # 顯示更詳細的診斷資訊，方便除錯
            try:
                import importlib
                mvmod = importlib.import_module("camera.MvImport.MvCameraControl_class")
                print("DEBUG MV_CC_EnumDevices diagnostics:")
                print("  hasattr(self.MvCamera, 'MV_CC_EnumDevices') =", hasattr(self.MvCamera, "MV_CC_EnumDevices"))
                print("  hasattr(mvmod, 'MV_CC_EnumDevices') =", hasattr(mvmod, "MV_CC_EnumDevices"))
                print("  hasattr(mvmod, 'MvCamCtrldll') =", hasattr(mvmod, "MvCamCtrldll"))
                if hasattr(mvmod, "MvCamCtrldll"):
                    print("  hasattr(mvmod.MvCamCtrldll, 'MV_CC_EnumDevices') =", hasattr(mvmod.MvCamCtrldll, "MV_CC_EnumDevices"))
            except Exception:
                pass
            raise RuntimeError(f"MV_CC_EnumDevices failed: {e}")

        if dev_list.nDeviceNum == 0:
            raise RuntimeError("No Hikvision camera found.")

        # 選擇設備（若提供 serial，嘗試匹配）
        selected_index = 0
        if self.serial:
            matched = None
            for i in range(dev_list.nDeviceNum):
                dev = dev_list.pDeviceInfo[i].contents
                # 嘗試讀取可能存在的序列號欄位
                try:
                    # 支援 GigE 與 USB 的不同結構
                    s = None
                    if dev.nTLayerType == self.MV_GIGE_DEVICE:
                        s = bytes(dev.SpecialInfo.stGigEInfo.chSerialNumber).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                    else:
                        s = bytes(dev.SpecialInfo.stUsb3VInfo.chSerialNumber).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                    if s == self.serial:
                        matched = i
                        break
                except Exception:
                    continue
            if matched is None:
                raise RuntimeError(f"Hikvision camera with serial {self.serial} not found.")
            selected_index = matched

        dev = dev_list.pDeviceInfo[selected_index].contents
        self.device_info = dev

        # 建立 handle 並開啟設備
        cam = self.MvCamera()
        rc = cam.MV_CC_CreateHandle(dev)
        if rc != 0:
            raise RuntimeError(f"MV_CC_CreateHandle failed: {rc}")
        rc = cam.MV_CC_OpenDevice()
        if rc != 0:
            raise RuntimeError(f"MV_CC_OpenDevice failed: {rc}")

        # 嘗試設定常用參數（節點名稱可能依型號不同而不同）
        try:
            cam.MV_CC_SetFloatValue(b"ExposureTime".decode('ascii'), float(self.exposure_us))
        except Exception as e:
            print(f"⚠️ 無法設定 ExposureTime: {e}")
        try:
            cam.MV_CC_SetFloatValue(b"Gain".decode('ascii'), float(self.gain))
        except Exception as e:
            print(f"⚠️ 無法設定 Gain: {e}")

        # 設定 FPS（若支援）
        try:
            cam.MV_CC_SetFloatValue(b"AcquisitionFrameRate".decode('ascii'), float(self.target_fps))
            print(f"✅ 嘗試設定 AcquisitionFrameRate = {self.target_fps}")
        except Exception as e:
            print(f"⚠️ 無法設定 AcquisitionFrameRate: {e}")

        # 設定取流策略為只保留最新一幀
        try:
            cam.MV_CC_SetGrabStrategy(self.MV_GrabStrategy_LatestImagesOnly)
        except Exception as e:
            print(f"⚠️ 無法設定 GrabStrategy: {e}")

        # 啟動取流
        try:
            cam.MV_CC_StartGrabbing()
        except Exception as e:
            print(f"❌ StartGrabbing failed: {e}")

        self.camera = cam

    def get_frame(self, timeout_ms=1000):
        """取得一張影像並轉為 numpy 陣列；回傳 None 表示失敗或逾時"""
        if not self.camera:
            print("[WARN] HK camera not opened.")
            return None

        st_frame = self.MV_FRAME_OUT()
        try:
            rc = self.camera.MV_CC_GetImageBuffer(st_frame, timeout_ms)
            if rc != 0:
                # 沒有取得影像
                return None

            w = int(st_frame.stFrameInfo.nWidth)
            h = int(st_frame.stFrameInfo.nHeight)
            ptype = int(st_frame.stFrameInfo.enPixelType)
            flen = int(st_frame.stFrameInfo.nFrameLen)
            pbuf = st_frame.pBufAddr

            # 將 ctypes buffer 轉 bytes
            import ctypes
            raw = ctypes.string_at(pbuf, flen)

            import numpy as _np
            if ptype == self.PixelType_Mono8:
                arr = _np.frombuffer(raw, dtype=_np.uint8)
                try:
                    arr = arr.reshape((h, w))
                except Exception:
                    return None
                return arr
            elif ptype == self.PixelType_BGR8:
                arr = _np.frombuffer(raw, dtype=_np.uint8)
                try:
                    arr = arr.reshape((h, w, 3))
                except Exception:
                    return None
                return arr
            else:
                print(f"⚠️ 不支援的像素格式: {ptype}，僅支援 Mono8 / BGR8。")
                return None
        finally:
            try:
                self.camera.MV_CC_FreeImageBuffer(st_frame)
            except Exception:
                pass

    def set_fps(self, target_fps):
        try:
            self.camera.MV_CC_SetFloatValue(b"AcquisitionFrameRate".decode('ascii'), float(target_fps))
            print(f"✅ 嘗試設定 FPS: {target_fps}")
        except Exception as e:
            print(f"⚠️ 設定 FPS 失敗: {e}")

    def get_camera_info(self):
        try:
            # 嘗試讀出型號與序列號
            info = {}
            try:
                if self.device_info.nTLayerType == self.MV_GIGE_DEVICE:
                    s_model = bytes(self.device_info.SpecialInfo.stGigEInfo.chModelName).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                    s_sn = bytes(self.device_info.SpecialInfo.stGigEInfo.chSerialNumber).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                else:
                    s_model = bytes(self.device_info.SpecialInfo.stUsb3VInfo.chModelName).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                    s_sn = bytes(self.device_info.SpecialInfo.stUsb3VInfo.chSerialNumber).split(b"\x00",1)[0].decode('ascii',errors='ignore')
                info["model"] = s_model
                info["serial"] = s_sn
            except Exception:
                info["model"] = "Unknown"
                info["serial"] = "Unknown"

            # 嘗試獲取目前 FPS
            try:
                # 直接透過節點查詢可能不可行，在此僅嘗試讀取節點值
                f = self.camera.MV_CC_GetFloatValue
                # 由於 SDK 的 MV_CC_GetFloatValue 需要 ctype 輸入與輸出，簡單跳過複雜讀取
            except Exception:
                pass

            return info
        except Exception as e:
            print(f"獲取 HK 相機資訊失敗: {e}")
            return None

    def measure_actual_fps(self, num_frames=100):
        print(f"📊 正在測量 HK 實際 FPS (採樣 {num_frames} 幀)...")
        start_time = time.time()
        frame_count = 0
        for _ in range(num_frames):
            frame = self.get_frame(timeout_ms=1000)
            if frame is not None:
                frame_count += 1
        elapsed = time.time() - start_time
        fps = frame_count / elapsed if elapsed > 0 else 0
        print(f"✅ 實測 HK FPS: {fps:.2f} ({frame_count}/{num_frames})")
        return fps

    def stop(self):
        if self.camera:
            try:
                self.camera.MV_CC_StopGrabbing()
            except Exception:
                pass
            try:
                self.camera.MV_CC_CloseDevice()
            except Exception:
                pass
            try:
                self.camera.MV_CC_DestroyHandle()
            except Exception:
                pass