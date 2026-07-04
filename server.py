"""
SecurePlate Web Server
Serves the HTML frontend and handles plate detection via API.

Run:
    python server.py
Then open http://localhost:5000 in your browser.
"""
import os
import base64
import threading
from datetime import datetime

import cv2
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from plate_recognition import read_plate_from_frame
from database_check import check_plate
from dashboard_update import update_dashboard

app = Flask(__name__)

# ---------------------------------------------------------------------------
# EasyOCR pre-warm
# Run in a daemon thread so the server starts instantly.
# Without this, the first /detect call blocks for ~30 s while the model loads.
# ---------------------------------------------------------------------------
_model_ready = threading.Event()

def _warm_up():
    try:
        print("[server] Pre-warming EasyOCR model — first /detect will be fast after this...")
        from plate_recognition import easyocr_read
        dummy = np.zeros((60, 200, 3), dtype=np.uint8)
        easyocr_read(dummy)
        print("[server] EasyOCR model ready ✓")
    except Exception as exc:
        print(f"[server] Warm-up error (non-fatal): {exc}")
    finally:
        _model_ready.set()   # signal ready regardless of success/failure

threading.Thread(target=_warm_up, daemon=True).start()

# --- Constants ---
CONFIDENCE_THRESHOLD = 0.35
LOG_FILE = 'log.csv'
IMAGES_DIR = 'images'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log_result(plate_text, status, confidence, image_path):
    """Append a detection event to log.csv."""
    try:
        record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'plate': plate_text,
            'status': status,
            'confidence': round(float(confidence), 4),
            'image_path': image_path,
        }
        df = pd.DataFrame([record])
        log_exists = os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0
        df.to_csv(LOG_FILE, mode='a', header=not log_exists, index=False)
    except Exception as e:
        print(f"[log_result] Error: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')


@app.route('/detect', methods=['POST'])
def detect():
    """
    Receive a base64-encoded JPEG frame from the browser,
    run plate detection, and return the result as JSON.

    Expected JSON body: { "image": "data:image/jpeg;base64,..." }
    """
    data = request.get_json(silent=True)
    if not data or 'image' not in data:
        return jsonify({'error': 'No image provided'}), 400

    try:
        # Strip the data-URL prefix (e.g. "data:image/jpeg;base64,")
        _, encoded = data['image'].split(',', 1)
        img_bytes = base64.b64decode(encoded)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception as e:
        return jsonify({'error': f'Image decode failed: {e}'}), 400

    if frame is None:
        return jsonify({'error': 'Invalid image data'}), 400

    # Run plate detection
    plate_text, conf, crop = read_plate_from_frame(frame)

    if plate_text and conf > CONFIDENCE_THRESHOLD:
        status, info = check_plate(plate_text)

        # Persist crop to disk
        os.makedirs(IMAGES_DIR, exist_ok=True)
        save_path = (
            f"{IMAGES_DIR}/{plate_text}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        )
        if crop is not None:
            cv2.imwrite(save_path, crop)

        log_result(plate_text, status, conf, save_path)
        update_dashboard(plate_text, status, info)

        return jsonify({
            'detected': True,
            'plate': plate_text,
            'confidence': round(float(conf), 4),
            'status': status,
            'info': info if isinstance(info, dict) else {},
        })

    return jsonify({'detected': False})


@app.route('/log')
def get_log():
    """Return the last 50 detection events from log.csv (newest first)."""
    try:
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
            df = pd.read_csv(LOG_FILE)
            records = df.tail(50).iloc[::-1].to_dict(orient='records')
            return jsonify(records)
        return jsonify([])
    except Exception as e:
        print(f"[get_log] Error: {e}")
        return jsonify([])


@app.route('/stats')
def get_stats():
    """Return aggregate counters for the dashboard stat cards."""
    try:
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
            df = pd.read_csv(LOG_FILE)
            total = len(df)
            allowed = int((df['status'] == 'ALLOWED').sum())
            denied = total - allowed
            return jsonify({'total': total, 'allowed': allowed, 'denied': denied})
        return jsonify({'total': 0, 'allowed': 0, 'denied': 0})
    except Exception as e:
        print(f"[get_stats] Error: {e}")
        return jsonify({'total': 0, 'allowed': 0, 'denied': 0})


@app.route('/allowed')
def get_allowed():
    """Return the full allowed-plates list."""
    try:
        if os.path.isfile('allowed_list.csv'):
            df = pd.read_csv('allowed_list.csv')
            return jsonify(df.to_dict(orient='records'))
        return jsonify([])
    except Exception as e:
        print(f"[get_allowed] Error: {e}")
        return jsonify([])


@app.route('/health')
def health():
    """Browser polls this to know when the OCR model has finished loading."""
    return jsonify({'ready': _model_ready.is_set()})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  SecurePlate Web Server")
    print("  Open  http://localhost:5000  in your browser")
    print("=" * 60 + "\n")
    # threaded=True lets Flask handle /log and /stats polls
    # concurrently with a long-running /detect request.
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)
