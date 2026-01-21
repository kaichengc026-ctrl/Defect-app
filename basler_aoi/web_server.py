from flask import Flask, render_template, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
import os
import base64
import logging
import threading
import json
from collections import deque
import numpy as np
import cv2
from datetime import datetime
from config.config import HOST, PORT


app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) # 關閉 Flask 的預設日誌輸出

# 這些變數會在 main.py 中導入並設定
current_prediction = None
prediction_lock = None
frame_queue = None
image_queue = None
FRAME_QUEUE_MAX = None
IMAGE_QUEUE_MAX = None
pause_event = None
last_defect_image = None
image_lock = None
save_options = None
save_lock = None
camera_control = None
camera_lock = None
# SSE / log 支援
defect_cond = None
defect_log = None  # deque of recent defects
defect_log_lock = None
DEFECT_LOG_MAX = 1000
# sequence for user-visible defect IDs (only increments on true defects)
defect_seq = 0
# sequence for every event (used to deliver all events including demo/OK samples)
event_seq = 0
defects_dir = None


def init_app(pred, pred_lock, f_queue, i_queue, f_max, i_max, p_event, last_img, img_lock, save_opt, save_lk, cam_ctrl, cam_lock, defect_condition=None, defects_dir_path=None):
    """初始化全局變數"""
    global current_prediction, prediction_lock, frame_queue, image_queue
    global FRAME_QUEUE_MAX, IMAGE_QUEUE_MAX, pause_event, last_defect_image, image_lock, save_options, save_lock, camera_control, camera_lock, defect_cond, defect_log, defect_log_lock, defects_dir
    
    current_prediction = pred
    prediction_lock = pred_lock
    frame_queue = f_queue
    image_queue = i_queue
    FRAME_QUEUE_MAX = f_max
    IMAGE_QUEUE_MAX = i_max
    pause_event = p_event
    last_defect_image = last_img
    image_lock = img_lock
    save_options = save_opt
    save_lock = save_lk
    camera_control = cam_ctrl
    camera_lock = cam_lock
    # init defect notification and log
    defect_cond = defect_condition
    if defect_log is None:
        defect_log = deque(maxlen=DEFECT_LOG_MAX)
    if defect_log_lock is None:
        defect_log_lock = threading.Lock()
    # 缺陷檔案根目錄（預設為當前工作目錄下的 defects）
    defects_dir = defects_dir_path or os.path.join(os.getcwd(), 'defects')
    os.makedirs(defects_dir, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/prediction')
def get_prediction():
    """獲取當前預測結果"""
    if prediction_lock is None:
        return jsonify({"class": "待機中", "confidence": 0.0})
    
    with prediction_lock:
        return jsonify({
            "class": current_prediction.get("class", "待機中"),
            "confidence": float(current_prediction.get("confidence", 0.0))
        })

@app.route('/api/last-image')
def get_last_image():
    """取得最新缺陷影像(Base64)"""
    
    if last_defect_image is None or image_lock is None:
        return jsonify({"image": None, "ts": None})
    with image_lock:
        return jsonify({
            "image": last_defect_image.get("b64"),
            "ts": last_defect_image.get("ts")
        })

@app.route('/api/save-defect-status')
def get_save_defect_status():
    if save_options is None or save_lock is None:
        return jsonify({"save_defect": False})
    with save_lock:
        return jsonify({"save_defect": bool(save_options.get("save_defect", False))})

@app.route('/api/save-defect', methods=['POST'])
def set_save_defect_status():
    if save_options is None or save_lock is None:
        return jsonify({"status": "error", "message": "系統未初始化"}), 500
    try:
        from flask import request
        body = request.get_json(force=True, silent=True) or {}
        enable = bool(body.get("enable", False))
        with save_lock:
            save_options["save_defect"] = enable
        return jsonify({"status": "success", "save_defect": enable})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/demo-mode-status')
def get_demo_mode_status():
    """取得 Demo 模式狀態（開/關）。"""
    if save_options is None or save_lock is None:
        return jsonify({"demo_mode": False})
    with save_lock:
        return jsonify({"demo_mode": bool(save_options.get("demo_mode", False))})

@app.route('/api/demo-mode', methods=['POST'])
def set_demo_mode_status():
    """切換 Demo 模式（開/關）。"""
    if save_options is None or save_lock is None:
        return jsonify({"status": "error", "message": "系統未初始化"}), 500
    try:
        from flask import request
        body = request.get_json(force=True, silent=True) or {}
        enable = bool(body.get("enable", False))
        with save_lock:
            save_options["demo_mode"] = enable
        return jsonify({"status": "success", "demo_mode": enable})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/camera-status')
def get_camera_status():
    if camera_control is None or camera_lock is None:
        return jsonify({"on": False})
    with camera_lock:
        return jsonify({"on": bool(camera_control.get("on", False))})

@app.route('/api/camera', methods=['POST'])
def set_camera_status():
    if camera_control is None or camera_lock is None:
        return jsonify({"status": "error", "message": "系統未初始化"}), 500
    try:
        from flask import request
        body = request.get_json(force=True, silent=True) or {}
        enable = bool(body.get("enable", False))
        with camera_lock:
            camera_control["on"] = enable
        state = "開啟" if enable else "關閉"
        print(f"🎛️ Camera {state}")
        return jsonify({"status": "success", "on": enable})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/queue-status')
def get_queue_status():
    """獲取隊列狀態"""
    if frame_queue is None or image_queue is None:
        return jsonify({
            "frame_queue": {"current": 0, "max": 0, "percentage": 0},
            "image_queue": {"current": 0, "max": 0, "percentage": 0}
        })
    
    frame_size = frame_queue.qsize()
    image_size = image_queue.qsize()
    
    return jsonify({
        "frame_queue": {
            "current": frame_size,
            "max": FRAME_QUEUE_MAX,
            "percentage": (frame_size / FRAME_QUEUE_MAX * 100) if FRAME_QUEUE_MAX > 0 else 0
        },
        "image_queue": {
            "current": image_size,
            "max": IMAGE_QUEUE_MAX,
            "percentage": (image_size / IMAGE_QUEUE_MAX * 100) if IMAGE_QUEUE_MAX > 0 else 0
        }
    })

@app.route('/api/pause-status')
def get_pause_status():
    """獲取暫停狀態"""
    if pause_event is None:
        return jsonify({"paused": False})
    
    # Event.is_set() = True 表示運行中，False 表示暫停
    return jsonify({"paused": not pause_event.is_set()})

@app.route('/api/pause', methods=['POST'])
def pause_system():
    """暫停系統"""
    if pause_event is None:
        return jsonify({"status": "error", "message": "系統未初始化"}), 500
    
    pause_event.clear()  # 清除 event = 暫停
    print("⏸️ 系統已暫停")
    return jsonify({"status": "success", "message": "系統已暫停", "paused": True})

@app.route('/api/resume', methods=['POST'])
def resume_system():
    """恢復系統"""
    if pause_event is None:
        return jsonify({"status": "error", "message": "系統未初始化"}), 500
    
    pause_event.set()  # 設置 event = 恢復運行
    print("▶️ 系統已恢復")
    return jsonify({"status": "success", "message": "系統已恢復", "paused": False})

# ----- Defect log recording and SSE push -----

def record_defect(b64, ts, filename=None, saved=False, class_name=None, confidence=None, metadata=None, is_demo=False):
    """在伺服器端記錄一筆缺陷事件並通知 SSE clients。
    支援將 metadata (dict) 一併寫入記錄，metadata 建議包含 box_count 與 boxes (YOLO 格式)。
    若 is_demo=True，該事件會被標記為 demo (仍會透過 SSE 推送)，但會從 /api/defect-logs 回傳結果中被過濾掉（不顯示在缺陷列表中）。
    """
    global defect_seq, defect_log, event_seq
    if defect_log is None:
        return
    # increment event_seq for every event (so SSE can deliver them reliably)
    with defect_log_lock:
        event_seq += 1
        entry = {
            "event_id": event_seq,
            "id": None,  # filled only for real defects (non-demo and not class '正常')
            "ts": ts,
            "saved": bool(saved),
            "filename": filename,
            "b64": b64,
            "class": class_name,
            "confidence": float(confidence) if confidence is not None else None,
            "metadata": metadata,
            "demo": bool(is_demo),
        }
        # assign visible defect id only for true defects
        if not entry.get('demo') and (class_name is not None) and (class_name != '正常'):
            defect_seq += 1
            entry['id'] = defect_seq
        defect_log.appendleft(entry)
    # notify SSE listeners
    if defect_cond is not None:
        with defect_cond:
            defect_cond.notify_all()
@app.route('/api/defect-metadata/<int:defect_id>')
def get_defect_metadata(defect_id):
    """取得指定缺陷的 metadata (JSON)"""
    if defect_log is None:
        return jsonify({"metadata": None}), 404
    with defect_log_lock:
        for e in defect_log:
            if e.get('id') == defect_id:
                return jsonify({"metadata": e.get('metadata')})
    return jsonify({"metadata": None}), 404

@app.route('/api/defect-test', methods=['POST'])
def defect_test():
    """簡易測試用：生成一張紅色圖像並產生 defect 事件（方便測試 SSE 與前端）"""
    ts = datetime.now().strftime("%Y%m%d_%H-%M-%S-%f")[:-3]
    try:
        img = np.zeros((200,200,3), dtype=np.uint8)
        img[:] = (0,0,255)
        ok, buf = cv2.imencode('.jpg', img)
        if not ok:
            return jsonify({"error": "failed to create image"}), 500
        b64 = 'data:image/jpeg;base64,' + base64.b64encode(buf.tobytes()).decode('ascii')
        # include dummy class/confidence for testing
        record_defect(b64=b64, ts=ts, filename=None, saved=False, class_name='TEST_DEFECT', confidence=0.88)
        return jsonify({"status": "ok", "ts": ts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/defect-stream')
def defect_stream():
    """SSE: 推送缺陷影像給前端（event: defect, data: JSON {id, ts, b64, saved, filename}）"""
    if defect_cond is None or defect_log is None:
        return jsonify({"error": "system not initialized"}), 500

    def event_stream():
        last_sent_event_id = 0
        # initial keep-alive
        yield ": connected\n\n"
        while True:
            with defect_cond:
                defect_cond.wait(timeout=15)
            # send all new entries since last_sent_event_id (include demo events)
            to_send = []
            with defect_log_lock:
                for entry in list(defect_log):
                    if entry.get("event_id", 0) > last_sent_event_id:
                        to_send.append(entry)
            # send in reverse (oldest first)
            for entry in reversed(to_send):
                last_sent_event_id = entry.get("event_id", last_sent_event_id)
                # Build payload (include demo flag; include b64 for demo events to allow immediate display)
                payload_obj = {
                    "id": entry.get("id"),
                    "ts": entry.get("ts"),
                    "saved": entry.get("saved"),
                    "filename": entry.get("filename"),
                    "class": entry.get("class"),
                    "confidence": entry.get("confidence"),
                    "metadata": entry.get("metadata"),
                    "demo": entry.get("demo", False)
                }
                if entry.get("demo") and entry.get("b64"):
                    payload_obj["b64"] = entry.get("b64")
                payload = json.dumps(payload_obj)
                yield f"event: defect\ndata: {payload}\n\n"
            # keep-alive ping
            yield ": keep-alive\n\n"

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

@app.route('/api/defect-logs')
def get_defect_logs():
    """返回缺陷記錄（不包含 b64，除非特別要求）。Demo 事件不會出現在此列表中。"""
    if defect_log is None:
        return jsonify({"logs": []})
    with defect_log_lock:
        logs = []
        for e in list(defect_log):
            if e.get('demo'):
                continue
            logs.append({
                "id": e.get("id"),
                "ts": e.get("ts"),
                "saved": e.get("saved"),
                "filename": e.get("filename"),
                "class": e.get("class"),
                "confidence": e.get("confidence"),
                "has_b64": True if e.get('b64') else False,
                "metadata": e.get("metadata")
            })
    return jsonify({"logs": logs})

@app.route('/api/defect-b64/<int:defect_id>')
def get_defect_b64(defect_id):
    """按 id 取得該缺陷的 base64（用於按需載入，節省 SSE 帶寬）"""
    if defect_log is None:
        return jsonify({"b64": None}), 404
    with defect_log_lock:
        for e in defect_log:
            if e.get('id') == defect_id:
                b64 = e.get('b64')
                if b64:
                    return jsonify({"b64": b64})
                else:
                    print(f"⚠️ get_defect_b64: id={defect_id} has no b64")
                    return jsonify({"b64": None}), 404
    print(f"⚠️ get_defect_b64: id={defect_id} not found (possibly evicted)")
    return jsonify({"b64": None}), 404

@app.route('/api/defect-image/<path:filename>')
def serve_defect_image(filename):
    """Serve saved defect image files from the defects directory"""
    global defects_dir
    if not defects_dir:
        return jsonify({"error": "defects directory not configured"}), 500
    if not os.path.exists(defects_dir):
        return jsonify({"error": "no defects directory"}), 404
    try:
        return send_from_directory(defects_dir, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

# ----- end defect log / SSE -----

def run_server():
    """在單獨的線程中運行 Flask 服務器"""
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
