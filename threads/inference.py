import threading
import numpy as np
from ultralytics import YOLO
import cv2
import os
from datetime import datetime
import time
import base64
import json


class InferenceThread(threading.Thread):
    def __init__(self, image_queue, model_path, pause_event, prediction, prediction_lock, last_image, image_lock, save_options, save_lock, defect_dir=None):
        super().__init__(daemon=True)
        self.image_queue = image_queue
        self.model = YOLO(model_path)
        self.pause_event = pause_event
        self.current_prediction = prediction
        self.prediction_lock = prediction_lock
        self.last_image = last_image
        self.image_lock = image_lock
        self.save_options = save_options
        self.save_lock = save_lock
        self.running = True
        # demo 推送速率控制 (秒)
        self._demo_interval = 0.5
        self._last_demo_push_ts = 0.0

        # 創建缺陷圖像保存目錄（支援外部傳入執行目錄）
        self.defect_dir = defect_dir or "defects"
        os.makedirs(self.defect_dir, exist_ok=True)

    def run(self):
        from queue import Empty
        
        while self.running:
            # 等待直到系統恢復運行（或超時後繼續檢查）
            if not self.pause_event.wait(timeout=0.1):
                continue
            
            # 使用 timeout 避免阻塞
            try:
                img = self.image_queue.get(timeout=0.1)
            except Empty:
                continue

            # 灰階線掃 → 轉 3 channel
            if img.ndim == 2:
                img = np.stack([img]*3, axis=-1)

            time.sleep(0.04)  # 模擬處理時間 (單位: 秒)
            results = self.model.predict(
                img,
                imgsz=640,
                conf=0.5,
                device=0,
                verbose=False
            )

            self.update_prediction(results)
            self.handle_result(results, img)

    def update_prediction(self, results):
        """更新全局預測結果"""
        if len(results[0].boxes) > 0:
            # 取得最高信心度的檢測結果
            box = results[0].boxes[0]
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            class_name = results[0].names[class_id]
            
            with self.prediction_lock:
                self.current_prediction["class"] = class_name
                self.current_prediction["confidence"] = confidence
        else:
            with self.prediction_lock:
                self.current_prediction["class"] = "正常"
                self.current_prediction["confidence"] = 0.0

    def handle_result(self, results, img):


        img_marked = self.draw_boxes(img, results)
        if len(results[0].boxes) > 0:
            # Defect detected: prepare timestamp and marked image
            timestamp = datetime.now().strftime("%Y%m%d_%H-%M-%S-%f")[:-3]
            

            # update last image and get base64 string
            b64 = self.update_last_image(img_marked, timestamp)

            # Build YOLO-format metadata for all detected boxes
            boxes = results[0].boxes
            h, w = img.shape[:2]
            yolo_boxes = []
            for b in boxes:
                x1, y1, x2, y2 = map(float, b.xyxy[0])
                cls_id = int(b.cls[0])
                cls_name = results[0].names[cls_id]
                product_area = w*h
                x_center = ((x1 + x2) / 2.0) / w
                y_center = ((y1 + y2) / 2.0) / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                yolo_boxes.append({
                    "class_id": cls_id,
                    "class_name": cls_name,
                    "x_center": x_center,
                    "y_center": y_center,
                    "width": bw,
                    "height": bh
                })
            metadata = {"box_count": len(yolo_boxes), "boxes": yolo_boxes}

            # get top detection info (class/confidence)
            box0 = results[0].boxes[0]
            class_id = int(box0.cls[0])
            class_name = results[0].names[class_id]
            confidence = float(box0.conf[0])

            # optionally save to disk and get filename
            filename = None
            with self.save_lock:
                save_defect_enabled = self.save_options.get("save_defect", True)
            if save_defect_enabled:
                filename = self.save_defect_image_to_disk(results, img_marked, timestamp, metadata=metadata)

            # record defect on server (append to log and notify SSE), include metadata
            try:
                import web_server
                web_server.record_defect(
                    b64=b64, 
                    ts=timestamp, 
                    filename=filename, 
                    saved=bool(filename), 
                    class_name=class_name, 
                    confidence=confidence, 
                    metadata=metadata)
            except Exception as e:
                print(f"⚠️ record_defect failed: {e}")
        else:
            # No defect detected: if demo mode enabled, schedule push via SSE (not immediate display)
            try:
                with self.save_lock:
                    demo_enabled = bool(self.save_options.get("demo_mode", False))
                if demo_enabled:
                    now_ts = time.time()
                    if now_ts - self._last_demo_push_ts < self._demo_interval:
                        # skip to avoid flooding server and client queues
                        return
                    self._last_demo_push_ts = now_ts
                    timestamp = datetime.now().strftime("%Y%m%d_%H-%M-%S-%f")[:-3]
                    # Encode current image to base64 for SSE/on-demand fetch
                    b64 = self.update_last_image(img_marked, timestamp)
                    # Record a 'normal' event into the log for queued display
                    try:
                        import web_server
                        web_server.record_defect(
                            b64=b64,
                            ts=timestamp,
                            filename=None,
                            saved=False,
                            class_name="正常",
                            confidence=0.0,
                            metadata={"box_count": 0, "boxes": []},
                            is_demo=True
                        )
                        # print(f"ℹ️ demo event enqueued: {timestamp}")
                    except Exception as e:
                        print(f"⚠️ record_defect(normal) failed: {e}")
            except Exception as e:
                print(f"⚠️ demo push failed: {e}")

    def save_defect_image_to_disk(self, results, img_marked, timestamp, metadata=None):
        """保存檢測到缺陷的圖像（僅負責寫檔，返回檔名或 None）。
        若提供 metadata 並且檔案儲存成功，會同時在 defects/ 下寫入同名的 .json metadata 檔案。
        """
        if not self.pause_event.is_set():
            print("⏸️  系統已暫停，跳過保存圖像")
            return None
        filename = None
        try:
            box = results[0].boxes[0]
            class_id = int(box.cls[0])
            class_name = results[0].names[class_id]
            confidence = float(box.conf[0])

            filename = f"{timestamp}_{class_name}_{confidence:.2f}.jpg"
            filepath = os.path.join(self.defect_dir, filename)

            if len(img_marked.shape) == 2:
                img_to_save = cv2.cvtColor(img_marked, cv2.COLOR_GRAY2BGR)
            else:
                img_to_save = img_marked

            ok = cv2.imwrite(filepath, img_to_save)
            if not ok:
                print(f"❌ 保存圖像失敗: cv2.imwrite returned False")
                return None

            # 如果提供 metadata，寫入同名的 .json 檔案
            if metadata is not None:
                try:
                    meta_filename = os.path.splitext(filename)[0] + '.json'
                    meta_path = os.path.join(self.defect_dir, meta_filename)
                    with open(meta_path, 'w', encoding='utf-8') as mf:
                        json.dump(metadata, mf, ensure_ascii=False, indent=2)
                except Exception as jerr:
                    print(f"⚠️ 無法寫入 metadata 檔案: {jerr}")

            print(f"✅ 缺陷圖像已保存: {filename}")
            return filename
        except Exception as e:
            print(f"❌ 保存圖像失敗: {e}")
            return None

    def update_last_image(self, img_to_save, timestamp):
        """更新最新缺陷影像（Base64，供 Web 顯示），並回傳 b64 字串或 None"""
        try:
            ok, buf = cv2.imencode('.jpg', img_to_save)
            if not ok:
                print("⚠️  轉為 JPEG 失敗")
                return None
            b64_img = base64.b64encode(buf.tobytes()).decode('ascii')
            b64_str = f"data:image/jpeg;base64,{b64_img}"
            with self.image_lock:
                self.last_image["b64"] = b64_str
                self.last_image["ts"] = timestamp
            return b64_str
        except Exception as enc_err:
            print(f"⚠️  轉為 Base64 失敗: {enc_err}")
            return None

    def draw_boxes(self, img, results):
        """在圖像上繪製檢測框"""
        img_copy = img.copy() if len(img.shape) == 3 else cv2.cvtColor(img.copy(), cv2.COLOR_GRAY2BGR)

        # convert boxes to list to avoid container-truthiness issues
        boxes = list(results[0].boxes)
        has_boxes = len(boxes) > 0
        # print(f"🔍 檢測到 {len(boxes)} 個缺陷") if has_boxes else print("✅ 無缺陷")

        if has_boxes:
            for box in boxes:
                # 獲取邊界框座標
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                confidence = float(box.conf[0])
                class_id = int(box.cls[0])
                class_name = results[0].names[class_id]
                
                # 繪製矩形框
                cv2.rectangle(img_copy, (x1, y1), (x2, y2), (0, 0, 255), 2)
                
                # 繪製標籤
                label = f"{class_name} {confidence:.2f}"
                cv2.putText(img_copy, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        # Draw status: if no boxes -> big green banner 'OK', else compact red 'NG' badge
        img_h, img_w = img_copy.shape[:2]

            # compact NG badge
        status_text = "NG" if has_boxes else "OK"
        bg_color = (0, 0, 200) if has_boxes else (0, 200, 0)
        text_color = (255, 255, 255)
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.9
        thickness = 2
        padding = 10

        (tw, th), _ = cv2.getTextSize(status_text, font, font_scale, thickness)
        x2 = img_w - padding
        x1 = max(0, x2 - tw - 2 * padding)
        y1 = padding
        y2 = min(img_h - 1, y1 + th + 2 * padding)

        cv2.rectangle(img_copy, (x1, y1), (x2, y2), bg_color, -1)
        cv2.rectangle(img_copy, (x1, y1), (x2, y2), (255,255,255), 1)
        text_x = x1 + padding
        text_y = y1 + padding + th - 2
        cv2.putText(img_copy, status_text, (text_x, text_y), font, font_scale, text_color, thickness, cv2.LINE_AA)

        return img_copy

    def stop(self):
        self.running = False