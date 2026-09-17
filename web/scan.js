// Camera scanner.
//
// Two decoders, because browser support is split and the phone in question
// runs Firefox: BarcodeDetector is native and fast but Chrome/Edge only, so
// everywhere else falls back to jsQR, vendored locally rather than pulled from
// a CDN so it also works with no network.
//
// This needs a secure context. Over plain http on a LAN address getUserMedia
// rejects, which is why the app is served through `tailscale serve`.

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

// A scanned value is a full URL -- https://host/b/B-0042. Read the trailing
// code segment and ignore the host, so a label still resolves if the server
// ever moves. Bare codes work too, for anything printed differently.
export function codeFrom(scanned) {
  const match = String(scanned).trim().match(/([A-Za-z]+-\d+)\/?$/);
  return match ? match[1].toUpperCase() : null;
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
      // Downscale: decoding a full 1080p frame every tick is needless work and
      // makes the phone hot. 480px on the long edge is plenty for a QR.
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
