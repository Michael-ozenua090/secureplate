/**
 * SecurePlate Dashboard — Client Logic
 * File: static/js/dashboard.js
 *
 * Features:
 *  - Live clock
 *  - Bulletproof camera fallback chain (never fails due to facingMode/resolution mismatch on Windows)
 *  - Camera enumeration (auto-detects how many cameras are available)
 *  - Camera start / stop / switch (switch button shown only when >1 camera)
 *  - Periodic frame capture → POST /detect
 *  - Visible scan feedback (frame counter, "No plate detected", "Plate found")
 *  - OCR model-ready polling via /health (shows a banner while loading)
 *  - Detection result rendering
 *  - Log table and stats counter polling
 */

/* -------------------------------------------------------
   STATE
------------------------------------------------------- */
let mediaStream  = null;
let scanInterval = null;
let scanning     = false;

/** All available video input devices (populated after camera permission is granted) */
let allCameras  = [];
/** Index of the currently active camera in allCameras */
let cameraIndex = 0;
/** Running count of frames sent to /detect */
let scanCount   = 0;

const video     = document.getElementById('video');
const capCanvas = document.getElementById('capture-canvas');
const capCtx    = capCanvas.getContext('2d');

/* -------------------------------------------------------
   CLOCK
------------------------------------------------------- */
const clockEl = document.getElementById('nav-clock');

function tick() {
    if (clockEl) clockEl.textContent = new Date().toLocaleTimeString('en-GB');
}
setInterval(tick, 1000);
tick();

/* -------------------------------------------------------
   MODEL HEALTH  —  poll /health until OCR model is ready
------------------------------------------------------- */
const modelBanner = document.getElementById('model-banner');

async function pollModelReady() {
    try {
        const res  = await fetch('/health');
        const data = await res.json();
        if (data && data.ready) {
            if (modelBanner) {
                modelBanner.style.background = 'rgba(0, 255, 136, 0.15)';
                modelBanner.style.borderColor = 'rgba(0, 255, 136, 0.4)';
                modelBanner.style.color = 'var(--success, #00ff88)';
                modelBanner.style.animation = 'none';
                modelBanner.innerHTML = '✓ PyTorch AI OCR Model is warmed up and ready for instant detection!';
                setTimeout(() => {
                    modelBanner.style.display = 'none';
                }, 3500);
            }
            return;   // done
        }
    } catch { /* server not up yet — keep polling */ }
    // Check again in 2 seconds
    setTimeout(pollModelReady, 2000);
}

// Start polling immediately on page load
pollModelReady();

/* -------------------------------------------------------
   BULLETPROOF CAMERA HELPER
   Windows desktop webcams often throw OverconstrainedError
   if facingMode: 'environment' or exact resolution is required.
   This fallback chain guarantees the camera opens if hardware exists.
------------------------------------------------------- */
async function openVideoDevice(deviceId) {
    const attempts = [];
    
    // 1. If a specific deviceId was requested (e.g. when switching cameras)
    if (deviceId) {
        attempts.push({ video: { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } } });
        attempts.push({ video: { deviceId: { exact: deviceId } } });
    }

    // 2. Fallbacks for default camera opening (tries 720p rear -> 720p any -> rear any -> any video)
    attempts.push({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'environment' } });
    attempts.push({ video: { width: { ideal: 1280 }, height: { ideal: 720 } } });
    attempts.push({ video: { facingMode: 'environment' } });
    attempts.push({ video: true });

    let lastErr = null;
    for (const constraints of attempts) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            console.log('[openVideoDevice] Success with constraints:', constraints);
            return stream;
        } catch (err) {
            console.warn('[openVideoDevice] Constraint failed, trying fallback:', constraints, err.name || err.message);
            lastErr = err;
        }
    }
    throw lastErr || new Error("Could not access any camera input device.");
}

/* -------------------------------------------------------
   CAMERA ENUMERATION
------------------------------------------------------- */

/**
 * Enumerate available video inputs.
 * NOTE: Device labels are only populated by browsers AFTER permission is granted.
 */
async function enumerateCameras() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        allCameras = devices.filter(d => d.kind === 'videoinput');
    } catch {
        allCameras = [];
    }

    const switchBtn = document.getElementById('btn-switch');
    if (switchBtn) {
        if (allCameras.length > 1) {
            switchBtn.style.display = 'inline-flex';
        } else {
            switchBtn.style.display = 'none';
        }
    }

    updateCameraLabel();
}

/**
 * Update the camera name shown in the footer.
 */
function updateCameraLabel() {
    const labelEl = document.getElementById('cam-label');
    if (!labelEl) return;

    const cam = allCameras[cameraIndex];
    if (!cam) {
        labelEl.textContent = '';
        return;
    }

    const name  = cam.label || `Camera ${cameraIndex + 1}`;
    const total = allCameras.length;
    labelEl.textContent = total > 1 ? `${name}  (${cameraIndex + 1}/${total})` : name;
}

/* -------------------------------------------------------
   CAMERA CONTROL
------------------------------------------------------- */
async function startCamera() {
    if (scanning) return;

    try {
        const targetCam = allCameras[cameraIndex];
        mediaStream = await openVideoDevice(targetCam ? targetCam.deviceId : null);
        video.srcObject = mediaStream;
        try { await video.play(); } catch (e) { console.warn("video.play() warning:", e); }

        // Now that permission is granted, enumerate again to get actual camera labels
        await enumerateCameras();

        // If cameraIndex wasn't set or out of bounds, align it with the active track if possible
        if (mediaStream && mediaStream.getVideoTracks().length > 0) {
            const activeTrack = mediaStream.getVideoTracks()[0];
            const settings = activeTrack.getSettings ? activeTrack.getSettings() : {};
            if (settings.deviceId) {
                const idx = allCameras.findIndex(c => c.deviceId === settings.deviceId);
                if (idx !== -1) cameraIndex = idx;
            }
        }
        updateCameraLabel();

        // Show scan UI
        document.getElementById('cam-standby').style.display = 'none';
        document.getElementById('scan-line').style.display   = 'block';
        document.getElementById('scan-dot').classList.add('active');
        document.getElementById('scan-text').textContent     = 'Scanning\u2026';
        document.getElementById('scan-text').style.color     = 'var(--accent)';
        document.getElementById('sys-dot').classList.remove('offline');
        document.getElementById('sys-label').textContent     = 'SCANNING';

        scanning  = true;
        scanCount = 0;

        // Send first frame immediately then every 2 s
        sendFrame();
        scanInterval = setInterval(sendFrame, 2000);

    } catch (err) {
        console.error('Camera error:', err);
        const errMsg = err.message || err.name || String(err);
        alert(
            `Could not access camera:\n${errMsg}\n\n` +
            `Make sure you have allowed camera permissions for localhost in your browser.`
        );
        document.getElementById('sys-dot').classList.add('offline');
        document.getElementById('sys-label').textContent = 'CAMERA ERROR';
        setScanFeedback('Error: ' + errMsg, 'miss');
    }
}

function stopCamera() {
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }
    clearInterval(scanInterval);
    scanInterval = null;
    scanning = false;

    video.srcObject = null;

    // Reset scan UI
    document.getElementById('cam-standby').style.display = 'flex';
    document.getElementById('scan-line').style.display   = 'none';
    document.getElementById('scan-dot').classList.remove('active');
    document.getElementById('scan-text').textContent     = 'Camera offline';
    document.getElementById('scan-text').style.color     = 'var(--t3)';
    document.getElementById('sys-label').textContent     = 'SYSTEM READY';
    document.getElementById('cam-label').textContent     = '';
    setScanFeedback('', '');

    // Hide switch button when camera is off
    const switchBtn = document.getElementById('btn-switch');
    if (switchBtn) switchBtn.style.display = 'none';
}

/* -------------------------------------------------------
   CAMERA SWITCHING
------------------------------------------------------- */
async function switchCamera() {
    if (!scanning || allCameras.length < 2) return;

    // Advance to next camera in the list (wraps around)
    const nextIndex = (cameraIndex + 1) % allCameras.length;
    const nextCam   = allCameras[nextIndex];

    // Stop current stream tracks without clearing the scan interval
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }

    try {
        mediaStream = await openVideoDevice(nextCam.deviceId);
        video.srcObject = mediaStream;
        try { await video.play(); } catch (e) { console.warn("video.play() warning:", e); }
        
        cameraIndex = nextIndex;
        updateCameraLabel();
        setScanFeedback(`\u2014 Switched to ${nextCam.label || ('Camera ' + (cameraIndex + 1))}`, 'hit');
    } catch (err) {
        console.error('[switchCamera] Failed:', err);
        setScanFeedback(`\u2014 Failed to switch camera`, 'miss');
        // Roll back and try to reopen previous camera
        try {
            const oldCam = allCameras[cameraIndex];
            mediaStream = await openVideoDevice(oldCam ? oldCam.deviceId : null);
            video.srcObject = mediaStream;
            await video.play();
        } catch {
            stopCamera();
        }
    }
}

/* -------------------------------------------------------
   FRAME CAPTURE → BACKEND
------------------------------------------------------- */
async function sendFrame() {
    if (!scanning || !mediaStream) return;
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        setScanFeedback(`\u2014 waiting for video feed (${video.readyState})\u2026`, 'miss');
        return;
    }

    scanCount++;

    // Draw current video frame to hidden canvas and encode as JPEG
    capCanvas.width  = video.videoWidth  || 1280;
    capCanvas.height = video.videoHeight || 720;
    capCtx.drawImage(video, 0, 0);
    const dataUrl = capCanvas.toDataURL('image/jpeg', 0.85);

    try {
        const res = await fetch('/detect', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ image: dataUrl })
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        handleResult(data, scanCount);

    } catch (err) {
        console.warn('[sendFrame] request failed:', err);
        setScanFeedback(`\u2014 error on frame #${scanCount}`, 'miss');
    }
}

/* -------------------------------------------------------
   DETECTION RESULT HANDLER
------------------------------------------------------- */
function handleResult(data, frame) {
    if (!data.detected) {
        // Give the user visible proof scans ARE running
        setScanFeedback(`\u2014 no plate \u00b7 frame #${frame}`, 'miss');
        return;
    }

    const pct     = Math.round((data.confidence ?? 0) * 100);
    const info    = data.info ?? {};
    const allowed = data.status === 'ALLOWED';

    // Plate number
    const plateEl = document.getElementById('plate-num');
    if (plateEl) {
        plateEl.textContent = data.plate ?? '---';
        plateEl.classList.add('lit');
    }

    // Status badge
    const badgeEl = document.getElementById('access-badge');
    if (badgeEl) {
        badgeEl.textContent = allowed ? '\u2713  ACCESS GRANTED' : '\u2717  ACCESS DENIED';
        badgeEl.className   = 'access-badge ' + (allowed ? 'allowed' : 'denied');
    }

    // Info fields
    const dOwner = document.getElementById('d-owner');
    const dCat   = document.getElementById('d-category');
    const dTime  = document.getElementById('d-time');
    if (dOwner) dOwner.textContent = info.owner    ?? '---';
    if (dCat)   dCat.textContent   = info.category ?? '---';
    if (dTime)  dTime.textContent  = new Date().toLocaleTimeString('en-GB');

    // Confidence bar
    const confFill = document.getElementById('conf-fill');
    const confPct  = document.getElementById('conf-pct');
    if (confFill) confFill.style.width = pct + '%';
    if (confPct)  confPct.textContent  = pct + '%';

    // Inline feedback
    setScanFeedback(`\u2014 \u2713 ${data.plate} (${pct}%)`, 'hit');

    // Flash camera card border
    const camCard = document.getElementById('cam-card');
    if (camCard) {
        camCard.classList.remove('flash');
        void camCard.offsetWidth;   // force reflow so animation restarts
        camCard.classList.add('flash');
    }

    // Refresh supporting data
    loadLog();
    loadStats();
}

/* -------------------------------------------------------
   SCAN FEEDBACK HELPER
------------------------------------------------------- */
function setScanFeedback(text, type) {
    const el = document.getElementById('scan-feedback');
    if (!el) return;
    el.textContent  = text;
    el.className    = 'scan-feedback' + (type ? ' ' + type : '');
}

/* -------------------------------------------------------
   DETECTION LOG
------------------------------------------------------- */
async function loadLog() {
    try {
        const res  = await fetch('/log');
        const rows = await res.json();
        renderLog(rows);
    } catch {
        /* Network hiccup — keep existing table content */
    }
}

function renderLog(rows) {
    const tbody = document.getElementById('log-body');
    if (!tbody) return;

    if (!rows || rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="log-empty">No detections logged yet</td></tr>';
        return;
    }

    tbody.innerHTML = rows.map(r => {
        const allowed = r.status === 'ALLOWED';
        const badge   = allowed
            ? '<span class="badge b-allowed">Allowed</span>'
            : '<span class="badge b-denied">Denied</span>';
        const pct = r.confidence != null
            ? Math.round(parseFloat(r.confidence) * 100) + '%'
            : '---';

        return `<tr>
            <td class="td-mono">${escHtml(String(r.timestamp ?? '---'))}</td>
            <td class="td-plate">${escHtml(String(r.plate ?? '---'))}</td>
            <td>${badge}</td>
            <td>${escHtml(String(r.owner ?? '---'))}</td>
            <td class="td-mono">${pct}</td>
        </tr>`;
    }).join('');
}

/* -------------------------------------------------------
   STATS COUNTERS
------------------------------------------------------- */
async function loadStats() {
    try {
        const res = await fetch('/stats');
        const s   = await res.json();
        const sTot = document.getElementById('s-total');
        const sAll = document.getElementById('s-allowed');
        const sDen = document.getElementById('s-denied');
        if (sTot) sTot.textContent   = s.total   ?? 0;
        if (sAll) sAll.textContent   = s.allowed ?? 0;
        if (sDen) sDen.textContent   = s.denied  ?? 0;
    } catch {
        /* Skip on error */
    }
}

/* -------------------------------------------------------
   UTILITY
------------------------------------------------------- */
function escHtml(str) {
    return str
        .replace(/&/g,  '&amp;')
        .replace(/</g,  '&lt;')
        .replace(/>/g,  '&gt;')
        .replace(/"/g,  '&quot;');
}

/* -------------------------------------------------------
   INITIALISE
------------------------------------------------------- */
loadLog();
loadStats();
enumerateCameras();
setInterval(loadStats, 15000);  // refresh stats every 15 s
setInterval(loadLog,   10000);  // refresh log   every 10 s
