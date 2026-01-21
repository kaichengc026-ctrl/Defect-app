import threading
import time

class LineGrabberThread(threading.Thread):
    def __init__(self, camera, line_queue):
        super().__init__(daemon=True)
        self.camera = camera      # 工業相機 SDK wrapper
        self.line_queue = line_queue
        self.running = True

    def run(self):
        self.camera.start()

        while self.running:
            line = self.camera.get_line()   # SDK: blocking / callback
            if line is None:
                continue

            try:
                self.line_queue.put_nowait(line)
            except:
                # Queue 滿 → 可以丟 line 或打 log
                # close camera for safety
                pass

    def stop(self):
        self.running = False
        self.camera.stop()


class FrameGrabberThread(threading.Thread):
    def __init__(self, camera, frame_queue, pause_event, camera_control, camera_lock):
        super().__init__(daemon=True)
        self.camera = camera
        self.q = frame_queue
        self.pause_event = pause_event
        self.camera_control = camera_control
        self.camera_lock = camera_lock
        self.running = True
        self.camera_running = False

    def run(self):
        # 如果主程式已經開啟相機，保持狀態
        try:
            self.camera_running = bool(self.camera.camera and self.camera.camera.IsGrabbing())
        except Exception:
            self.camera_running = False

        while self.running:
            with self.camera_lock:
                camera_on = bool(self.camera_control.get("on", True))

            if not camera_on:
                if self.camera_running:
                    try:
                        self.camera.stop()
                    except Exception:
                        pass
                    self.camera_running = False
                time.sleep(0.2)
                continue

            if not self.camera_running:
                try:
                    self.camera.start()
                    self.camera_running = True
                except Exception as e:
                    print(f"[WARN] 相機啟動失敗: {e}")
                    time.sleep(0.5)
                    continue

            # 等待直到系統恢復運行（或超時後繼續檢查）
            if not self.pause_event.wait(timeout=0.1):
                continue

            frame = self.camera.get_frame()
            if frame is None:
                continue
            try:
                self.q.put_nowait(frame)
            except Exception:
                # queue 滿了：丟掉舊 frame（維持低延遲）
                pass

        # 收尾：確保相機停止
        if self.camera_running:
            try:
                self.camera.stop()
            except Exception:
                pass

    def stop(self):
        self.running = False