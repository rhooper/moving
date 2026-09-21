// Camera scanner. BarcodeDetector is Chrome/Edge only, so elsewhere (Firefox)
// it falls back to jsQR, vendored so scanning works offline. Needs a secure
// context: on a plain-http LAN address getUserMedia rejects.

let decoder = null;

async function makeDecoder() {
  if ("BarcodeDetector" in window) {
    try {
      const formats = await window.BarcodeDetector.getSupportedFormats();
      if (formats.includes("qr_code")) {
        const native = new window.BarcodeDetector({ formats: ["qr_code"] });
        return async (canvas) => {
          const found = await native.detect(canvas);
          return found.length ? found[0].rawValue : null;
        };
      }
    } catch { /* fall through to jsQR */ }
  }

  await import("/jsQR.js");
  const jsQR = window.jsQR;
  return (canvas) => {
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const image = context.getImageData(0, 0, canvas.width, canvas.height);
    const found = jsQR(image.data, image.width, image.height, { inversionAttempts: "dontInvert" });
    return found ? found.data : null;
  };
}

// A scanned URL (https://host/b/CAM-001) gives the segment after /b/, whatever
// the host; a bare code works too. No assumption about the code's shape: the
// format is configurable (CAM-001, D001, Z06-001).
const SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;

export function codeFrom(scanned) {
  const raw = String(scanned ?? "").trim();
  if (!raw) return null;

  let candidate = raw;
  if (/^https?:\/\//i.test(raw)) {
    let path;
    try {
      path = new URL(raw).pathname;
    } catch {
      return null;
    }
    const parts = path.split("/").filter(Boolean);
    const marker = parts.lastIndexOf("b");
    // Without /b/, an unrelated QR's last path segment would pass for a code.
    if (marker === -1 || marker === parts.length - 1) return null;
    candidate = parts[marker + 1];
  }

  return SEGMENT.test(candidate) ? candidate.toUpperCase() : null;
}

export async function viewScan(show, showError) {
  show(`
    <h1 class="code">Scan</h1>
    <p class="meta" id="scan-status">Starting the camera…</p>
    <video id="cam" playsinline muted
           style="width:100%;aspect-ratio:3/4;object-fit:cover;background:var(--sunk)"></video>
    <div class="section">
      <h2>Or type the code</h2>
      <form id="manual" class="row">
        <input name="code" placeholder="B-0042" aria-label="Box code"
               autocapitalize="characters" autocomplete="off">
        <button class="btn" type="submit">Open</button>
      </form>
    </div>`);

  document.getElementById("manual").addEventListener("submit", (event) => {
    event.preventDefault();
    const typed = new FormData(event.target).get("code").trim();
    const code = codeFrom(typed) || typed.toUpperCase();
    if (code) location.hash = `#/b/${code}`;
  });

  const video = document.getElementById("cam");
  const status = document.getElementById("scan-status");

  if (!window.isSecureContext) {
    status.textContent =
      "The camera needs a secure connection. Open this page over https, not a plain LAN address.";
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
    });
  } catch (error) {
    status.textContent =
      error.name === "NotAllowedError"
        ? "Camera access was declined. Allow it in the site settings, or type the code below."
        : `The camera would not start (${error.name}). Type the code below instead.`;
    return;
  }

  video.srcObject = stream;
  await video.play().catch(() => {});
  decoder = decoder || (await makeDecoder());
  status.textContent = "Point it at a label.";

  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  let running = true;

  const stop = () => {
    running = false;
    stream.getTracks().forEach((track) => track.stop());
    removeEventListener("hashchange", stop);
  };
  addEventListener("hashchange", stop);

  async function tick() {
    if (!running) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      // 480px on the long edge is plenty for a QR; a full frame per tick heats the phone.
      const scale = 480 / Math.max(video.videoWidth, video.videoHeight);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      try {
        const raw = await decoder(canvas);
        const code = raw && codeFrom(raw);
        if (code) {
          stop();
          if (navigator.vibrate) navigator.vibrate(30);
          location.hash = `#/b/${code}`;
          return;
        }
      } catch (error) {
        stop();
        showError(`Could not read the camera: ${error.message}`);
        return;
      }
    }
    requestAnimationFrame(tick);
  }
  tick();
}
