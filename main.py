import cv2
import os
from datetime import datetime
from plate_recognition import read_plate_from_frame
from database_check import check_plate
from dashboard_update import update_dashboard
import pandas as pd
import time

# --- Configuration Constants ---
DETECTION_COOLDOWN = 2      # Seconds to wait between successive detections
CONFIDENCE_THRESHOLD = 0.5  # Minimum OCR confidence to act on a reading


def log_result(plate_text, status, confidence, image_path):
    """Log the plate detection result to CSV file."""
    try:
        log_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'plate': plate_text,
            'status': status,
            'confidence': confidence,
            'image_path': image_path
        }
        df = pd.DataFrame([log_data])
        # Use isfile + getsize to avoid a race condition on the header flag:
        # os.path.exists() returns True even for a zero-byte file, which would
        # suppress the header and produce an invalid CSV.
        log_exists = os.path.isfile('log.csv') and os.path.getsize('log.csv') > 0
        df.to_csv('log.csv', mode='a', header=not log_exists, index=False)
    except Exception as e:
        print(f"Error logging result: {str(e)}")


def draw_plate_info(frame, plate_text, status, conf):
    """Draw plate information on the frame."""
    color = (0, 255, 0) if status == "ALLOWED" else (0, 0, 255)

    # Draw semi-transparent background for better text visibility
    overlay = frame.copy()
    cv2.rectangle(overlay, (5, 5), (400, 80), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)

    # Draw text
    cv2.putText(frame, f"Plate: {plate_text}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    cv2.putText(frame, f"Status: {status}", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def main():
    # Ensure required directories exist (inside main to avoid import side-effects)
    os.makedirs('images', exist_ok=True)

    # Initialize video capture
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open video capture device")
        return

    # Set optimal camera properties for license plate detection
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

    last_detection_time = 0

    print("SecurePlate system started. Press 'q' to quit.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to grab frame")
                time.sleep(1)
                continue

            current_time = time.time()
            display_frame = frame.copy()

            # Only process frames if enough time has passed since last detection
            if current_time - last_detection_time >= DETECTION_COOLDOWN:
                plate_text, conf, crop = read_plate_from_frame(frame)

                if plate_text and conf > CONFIDENCE_THRESHOLD:
                    last_detection_time = current_time

                    # Save the cropped plate image
                    save_path = f'images/{plate_text}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
                    if crop is not None:
                        cv2.imwrite(save_path, crop)

                    # Process detection
                    status, info = check_plate(plate_text)
                    log_result(plate_text, status, conf, save_path)
                    update_dashboard(plate_text, status, info)

                    # Draw plate information
                    draw_plate_info(display_frame, plate_text, status, conf)

            # Display the frame
            cv2.imshow('SecurePlate - Press Q to Quit', display_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
