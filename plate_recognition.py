import os
import time
import cv2
import numpy as np
import easyocr
import re

# Lazy-initialized on first call to easyocr_read() to avoid blocking module import
reader = None


def _is_valid_plate_format(text: str) -> bool:
    """Check if text matches a valid license plate format.
    
    Standard Nigerian format: ABC-123DE (3 letters, 3 digits, 2 letters = 8 chars)
    Also accepts relaxed alphanumeric plate strings (5-10 chars with digits & letters)
    to handle custom plates, older formats, or minor OCR character drops.
    """
    if not text:
        return False
    
    text = text.upper().strip()
    normalized = text.replace('-', '').replace(' ', '')
    
    # 1. Exact Nigerian plate format: 3 letters + 3 digits + 2 letters = 8 chars
    if re.match(r'^[A-Z]{3}[0-9]{3}[A-Z]{2}$', normalized):
        return True
    
    # 2. Relaxed format: 5 to 10 alphanumeric chars with at least 1 digit and 2 letters
    if 5 <= len(normalized) <= 10 and re.search(r'[0-9]', normalized) and len(re.findall(r'[A-Z]', normalized)) >= 2:
        return True
    
    return False


def _normalize_plate(raw_text: str) -> str:
    """Normalize OCR text to a Nigerian license plate string (ABC-123DE format).

    Strategy:
    1. Uppercase the text and remove hyphens/spaces
    2. Extract alphanumeric tokens
    3. Try to find a token matching Nigerian format (3 letters + 3 digits + 2 letters)
    4. Try combining adjacent tokens if single tokens don't match
    5. Filter out state names (LAGOS, KADUNA, etc.)
    6. Return the matched plate or empty string if no valid plate found
    """
    if not raw_text:
        return ""

    text = raw_text.upper()
    # Remove separators, keep only alphanumeric
    text = re.sub(r"[^A-Z0-9]", " ", text)
    tokens = [t for t in text.split() if t and len(t) >= 2]
    
    if not tokens:
        return ""

    # Common Nigerian state names to skip
    state_names = {
        'LAGOS', 'KADUNA', 'KANO', 'KATSINA', 'KEBBI', 'KOGI', 'KWARA',
        'OGUN', 'ONDO', 'OSUN', 'OYO', 'PLATEAU', 'RIVERS', 'TARABA',
        'YOBE', 'ZAMFARA', 'ABIA', 'ADAMAWA', 'AKWA', 'BAUCH', 'BAYELSA',
        'BORNO', 'CROSS', 'DELTA', 'EBONYI', 'ENUGU', 'FCT', 'GOMBE', 'IMO',
        'JIGAWA', 'LACOS'
    }
    
    # First pass: look for exact Nigerian plate format in single tokens (3L-3D-2L = 8 chars)
    for token in tokens:
        clean_token = token.replace('-', '').replace(' ', '')
        if _is_valid_plate_format(clean_token):
            return clean_token
    
    # Second pass: try combining adjacent tokens
    # (e.g., "ABC" + "123DE", or "ABC" + "123" + "DE")
    if len(tokens) >= 2:
        # Try 2-token combinations
        for i in range(len(tokens) - 1):
            combined = (tokens[i] + tokens[i + 1]).replace('-', '').replace(' ', '')
            if _is_valid_plate_format(combined):
                return combined
    
    if len(tokens) >= 3:
        # Try 3-token combinations
        for i in range(len(tokens) - 2):
            combined = (tokens[i] + tokens[i + 1] + tokens[i + 2]).replace('-', '').replace(' ', '')
            if _is_valid_plate_format(combined):
                return combined
    
    # Fallback: return longest non-state token with mixed alphanumeric
    plate_tokens = [t for t in tokens if t not in state_names]
    if plate_tokens:
        alnum_tokens = [t for t in plate_tokens if re.search(r"[A-Z]", t) and re.search(r"[0-9]", t)]
        if alnum_tokens:
            longest = max(alnum_tokens, key=len)
            # Only return if it looks like part of a plate (has 4+ chars)
            if len(longest) >= 4:
                return longest
    
    return ""


def _repair_plate(raw: str) -> str:
    """Attempt to repair OCR output into a valid Nigerian plate string.

    Strategy:
    - Uppercase and remove non-alnum
    - If already valid, return
    - If length matches 8 but contains common confusions, apply position-based mapping
    - For digit positions (3-5) map likely letter confusions to digits: O->0, I/L->1, Z->2, S->5, B->8, G->6
    - For letter positions map digit confusions to letters (0->O,1->I)
    - Return repaired string if it matches the Nigerian plate regex, else empty
    """
    if not raw:
        return ""
    s = re.sub(r'[^A-Z0-9]', '', raw.upper())
    if _is_valid_plate_format(s):
        return s

    # If length not 8, try to pad/trim heuristics - only proceed when length is 8
    if len(s) != 8:
        return ""

    # Position mapping
    s_list = list(s)

    digit_map = {'O': '0', 'D': '0', 'Q': '0', 'I': '1', 'L': '1', 'Z': '2', 'S': '5', 'B': '8', 'G': '6'}
    # Map digit-like characters in letter positions back to their letter equivalents
    # Note: 'O' is already a valid letter so it has no mapping here
    letter_map = {'0': 'O', '1': 'I', '5': 'S', '2': 'Z', '8': 'B', '6': 'G'}

    # Apply digit maps for positions 3-5
    for i in range(3, 6):
        ch = s_list[i]
        if ch in digit_map:
            s_list[i] = digit_map[ch]

    # Apply letter maps for positions 0-2 and 6-7
    for i in [0, 1, 2, 6, 7]:
        ch = s_list[i]
        if ch in letter_map:
            s_list[i] = letter_map[ch]

    candidate = ''.join(s_list)
    if _is_valid_plate_format(candidate):
        return candidate

    return ""


def read_plate_aggregated(frame, buffer: list, max_len: int = 5, min_votes: int = 2, min_conf: float = 0.6, debug: bool = False):
    """Read a plate from a frame and update a rolling buffer of recent reads.

    Parameters:
    - frame: BGR image
    - buffer: a list object provided by the caller; this function appends (plate, conf, crop)
    - max_len: maximum length of buffer
    - min_votes: minimum identical readings required to accept a plate
    - min_conf: minimum confidence for a single-frame acceptance

    Returns (plate, conf, crop) when a decision is reached, otherwise (None,0,None).
    The buffer is modified in-place.
    """
    try:
        plate, conf, crop = read_plate_from_frame(frame, debug=debug)
    except Exception as e:
        if debug:
            print(f"Aggregated read error: {e}")
        plate, conf, crop = None, 0, None

    # Normalize empty
    plate = plate if plate else None

    # Append to buffer
    buffer.append((plate, float(conf), crop))
    # Trim buffer
    if len(buffer) > max_len:
        del buffer[0: len(buffer) - max_len]

    # Count votes (ignore None)
    counts = {}
    confs = {}
    last_crop = {}
    for p, c, cr in buffer:
        if not p:
            continue
        counts[p] = counts.get(p, 0) + 1
        confs[p] = max(confs.get(p, 0.0), float(c))
        last_crop[p] = cr

    # If any plate has enough votes, accept it
    for p, cnt in counts.items():
        if cnt >= min_votes:
            if debug:
                print(f"Aggregated: accepted '{p}' by votes={cnt}")
            return p, confs.get(p, 0.0), last_crop.get(p)

    # Otherwise, accept the best-confidence single read if it's high enough
    best_plate = None
    best_conf = 0.0
    for p, c in confs.items():
        if c > best_conf:
            best_conf = c
            best_plate = p

    if best_plate and best_conf >= min_conf:
        if debug:
            print(f"Aggregated: accepted '{best_plate}' by confidence={best_conf}")
        return best_plate, best_conf, last_crop.get(best_plate)

    # No decision yet
    return None, 0, None


def easyocr_read(image):
    """Run EasyOCR and return a normalized plate string plus confidence.
    (Simplified for performance)
    """
    # Lazy-initialize the EasyOCR reader on first call
    global reader
    if reader is None:
        reader = easyocr.Reader(['en'])

    # --- Preprocessing ---
    # Convert to gray
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    
    # Resize: make width ~600 px for better OCR performance
    h, w = gray.shape[:2]
    target_w = 600
    if w > 0 and w < target_w: # Add w > 0 check to avoid division by zero
        scale = target_w / float(w)
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)
    
    # --- OCR Attempts ---
    candidates = [] # list of (normalized_text, confidence)
    best_raw_text = ""
    best_raw_conf = 0.0

    # We will try just two variants:
    # 1. CLAHE-enhanced grayscale (good for most conditions)
    # 2. Inverted CLAHE (good for blue-on-white plates)
    variants = {
        "clahe": gray_clahe,
        "inverted": 255 - gray_clahe # Invert the CLAHE image
    }

    for variant_name, img_to_read in variants.items():
        try:
            # We must pass a BGR-like image to easyocr, even if it's gray
            bgr_img = cv2.cvtColor(img_to_read, cv2.COLOR_GRAY2BGR)
            result = reader.readtext(bgr_img)
        except Exception as ocr_err:
            # Log OCR failures per-variant; other variants may still succeed
            print(f"[EasyOCR] Error on variant '{variant_name}': {ocr_err}")
            result = []

        # Extract and combine all text blocks found by EasyOCR into a single string
        texts = []
        confs = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                text_frag = str(item[1] if item[1] is not None else "").strip()
                if text_frag:
                    texts.append(text_frag)
                conf_val = float(item[2] if item[2] is not None else 0.0)
                confs.append(conf_val)

        if not texts:
            continue

        combined_text = " ".join(texts)
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        # Store the best raw read (for repair later)
        if avg_conf > best_raw_conf:
            best_raw_conf = avg_conf
            best_raw_text = combined_text

        # Check if the entire combined string can be normalized into a valid plate
        normalized = _normalize_plate(combined_text)
        if normalized:
            candidates.append((normalized, avg_conf))

    # --- Process Results ---

    # If we have valid normalized candidates, pick the best one
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        plate, conf = candidates[0]
        # Final repair step
        plate_fixed = _repair_plate(plate)
        return plate_fixed, conf

    # If no normalized candidate, try to repair the best raw read
    if best_raw_text:
        repaired = _repair_plate(best_raw_text)
        if repaired:
            return repaired, best_raw_conf

    return "", 0


def crop_from_contour(frame, contour):
    """Extract and perspective-correct a region from a 4-point contour."""
    
    # Order the 4 points (contour) in a predictable
    # top-left, top-right, bottom-right, bottom-left order
    def order_points(pts):
        rect = np.zeros((4, 2), dtype="float32")
        
        # 1. Find top-left (smallest x+y sum)
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        
        # 2. Find bottom-right (largest x+y sum)
        rect[2] = pts[np.argmax(s)]
        
        # 3. Find top-right (smallest y-x diff)
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        
        # 4. Find bottom-left (largest y-x diff)
        rect[3] = pts[np.argmax(diff)]
        
        return rect

    # --- Start of main function ---
    try:
        x, y, w, h = cv2.boundingRect(contour)
        
        # If contour is a 4-point polygon, use it for perspective warp
        if len(contour) == 4:
            pts = contour.reshape(4, 2).astype(np.float32)
            
            # Order the points
            rect_pts = order_points(pts)
            (tl, tr, br, bl) = rect_pts
            
            # --- Calculate the new width and height ---
            # Width is the max distance between (bottom-right & bottom-left)
            # or (top-right & top-left)
            widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
            widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
            maxWidth = max(int(widthA), int(widthB))
            
            # Height is the max distance between (top-right & bottom-right)
            # or (top-left & bottom-left)
            heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
            heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
            maxHeight = max(int(heightA), int(heightB))

            # Define destination corners for a rectified view
            # We use the *calculated* maxWidth and maxHeight to get the
            # correct aspect ratio.
            dst_pts = np.array([
                [0, 0],
                [maxWidth - 1, 0],
                [maxWidth - 1, maxHeight - 1],
                [0, maxHeight - 1]
            ], dtype=np.float32)
            
            # Get perspective transformation matrix and apply it
            matrix = cv2.getPerspectiveTransform(rect_pts, dst_pts)
            warped = cv2.warpPerspective(frame, matrix, (maxWidth, maxHeight))
            
            # Add a check for a valid warped image
            if warped.size == 0 or maxWidth == 0 or maxHeight == 0:
                raise ValueError("Warped image is empty")
                
            return warped
    
    except Exception as e:
        # Fallback to simple rectangular crop on any error
        # Note: frame is always a parameter so it is always in scope here
        h_frame, w_frame = frame.shape[:2]
        if 'w' not in locals() or 'h' not in locals():  # boundingRect failed; nothing to crop
            return np.array([])
            
        pad_x = int(w * 0.06)
        pad_y = int(h * 0.06)
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(w_frame, x + w + pad_x)
        y1 = min(h_frame, y + h + pad_y)
        return frame[y0:y1, x0:x1]

    # Default fallback if logic fails
    x, y, w, h = cv2.boundingRect(contour)
    return frame[y:y+h, x:x+w]


def read_plate_from_frame(frame, debug=False):
    """Detect a license plate in the frame and return the recognized text.
    
    Filters contours by aspect ratio and size to prefer plate-like rectangles.
    License plates are typically wider than tall (aspect ratio ~2.5:1 to 4:1).
    Skips state-name-only detections.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Bilateral filter is excellent at removing noise while preserving edges
    blur = cv2.bilateralFilter(gray, 11, 17, 17) 

    # Canny edge detection with more selective thresholds.
    # We are looking for the plate *border*, not faint inner characters.
    edged = cv2.Canny(blur, 50, 200) 

    # Optional: Add a morphological closing step to fill gaps in the plate edge
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel, iterations=1)

    # Find all rectangular contours using RETR_LIST so we evaluate all rectangular shapes,
    # including nested contours (like a plate inside a car bumper).
    contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    
    # Filter and sort contours by area (largest first)
    candidates = []
    for i, c in enumerate(contours):
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.018*peri, True)
        
        # Check if contour is roughly rectangular (4 corners)
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            area = w * h
            
            # Filter by size: plate should be reasonably large
            # Lower threshold (300) to detect smaller plates in frame
            if area < 300:
                continue
            
            # Filter by aspect ratio. Standard Nigerian plates have a ratio of ~1.875.
            # We use a relaxed range from 1.5 to 5.5 to account for slight camera tilt or tight bounding boxes.
            aspect_ratio = w / (h + 1e-6)
            if not (1.5 <= aspect_ratio <= 5.5):
                continue
            
            # Position filter: avoid very top of frame (where state label usually is)
            frame_height = frame.shape[0]
            if y < frame_height * 0.05:  # Skip if in top 5%
                continue
            
            if debug:
                print(f"Contour {i}: area={area}, aspect={aspect_ratio:.2f}, pos=({x},{y})")
            
            candidates.append((area, approx, c))
    
    # Sort by area (largest first) to prefer bigger, more likely plates
    candidates.sort(reverse=True, key=lambda x: x[0])
    
    if debug:
        print(f"Found {len(candidates)} valid plate candidates")

    # Try candidates (quadrilaterals) first
    for idx, (area, approx, c) in enumerate(candidates):
        crop = crop_from_contour(frame, approx)
        if crop.size == 0:
            continue

        # Save candidate crop for debugging
        if debug:
            os.makedirs('debug_crops', exist_ok=True)
            fname = f"debug_crops/candidate_{int(time.time())}_{idx}_{int(area)}.jpg"
            try:
                cv2.imwrite(fname, crop)
                print(f"Saved candidate crop: {fname}")
            except Exception:
                pass

        text, conf = easyocr_read(crop)
        if debug:
            print(f"Candidate {idx}: raw_text='{text}', conf={conf:.3f}")

        # Validate the detected text (easyocr_read already normalizes)
        if text and _is_valid_plate_format(text):
            if debug:
                print(f"✓ Valid plate detected: '{text}'")
            return text, conf, crop
        else:
            if debug and text:
                print(f"✗ Text '{text}' rejected by format check")

    # If no quadrilateral candidates produced a valid plate, try a fallback:
    # OCR the largest rectangular bounding boxes (this helps when approxPolyDP fails)
    if debug:
        print("No quad candidates produced a valid plate, trying rectangular bounding-box fallback")

    # Build list of bounding rects and sort by area
    rects = []
    for i, c in enumerate(contours):
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < 800:
            continue
        rects.append((area, x, y, w, h))
    rects.sort(reverse=True, key=lambda x: x[0])

    # Try OCR on top N rects
    for i, (area, x, y, w, h) in enumerate(rects[:6]):
        crop = frame[y:y+h, x:x+w]
        if crop.size == 0:
            continue

        # Save fallback crop for debugging
        if debug:
            os.makedirs('debug_crops', exist_ok=True)
            fname = f"debug_crops/fallback_{int(time.time())}_{i}_{int(area)}.jpg"
            try:
                cv2.imwrite(fname, crop)
                print(f"Saved fallback crop: {fname}")
            except Exception:
                pass

        text, conf = easyocr_read(crop)
        if debug:
            print(f"Fallback rect {i}: area={area}, pos=({x},{y}), raw='{text}', conf={conf:.3f}")

        if text and _is_valid_plate_format(text):
            if debug:
                print(f"✓ Fallback valid plate: '{text}'")
            return text, conf, crop

    # --- Final Fallback: Full-Frame / Center-Crop OCR ---
    # If contour detection or rectangular cropping didn't locate a valid plate box (e.g. webcam glare or photos),
    # let EasyOCR's neural network scan the entire frame directly!
    if debug:
        print("No bounding rect produced a valid plate, running EasyOCR directly on full frame...")
    text, conf = easyocr_read(frame)
    if debug:
        print(f"Full-frame OCR result: raw='{text}', conf={conf:.3f}")
    if text and _is_valid_plate_format(text):
        if debug:
            print(f"✓ Full-frame valid plate detected: '{text}'")
        return text, conf, frame

    if debug:
        print("No valid plate found")
    return None, 0, None
