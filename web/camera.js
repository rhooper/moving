// The live camera, and what it is allowed to say.
//
// One camera for the two places that take a photograph before there is a
// record to hang it on: the "Add something inside" dialog and the new-record
// form. The DOM half is `photoField` in app.js; these are the decisions, kept
// here so they are made once and can be tested.
//
//
// Every camera failure is ordinary: the file picker is still there, and the
// line says which happened. Insecure is decided *before* asking, because on a
// plain LAN address getUserMedia rejects with nothing useful.
export function cameraTrouble(error, { secure = true } = {}) {
  const instead = "Choose a photo instead.";
  if (!secure) {
    return `The camera needs a secure connection, so there is no viewfinder here. ${instead}`;
  }
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return `Camera access was declined. ${instead} You can allow it in this site's settings.`;
    case "NotFoundError":
    case "OverconstrainedError":
      return `No camera was found. ${instead}`;
    case "NotReadableError":
      return `The camera is already in use somewhere else. ${instead}`;
    default:
      return `The camera would not start${error?.name ? ` (${error.name})` : ""}. ${instead}`;
  }
}

// With no size asked for, the browser gives its default 640x480. Square, so
// it reads the same whichever way the phone is held; the browser picks its
// nearest real mode.
export const CAMERA_REQUEST = {
  video: { facingMode: { ideal: "environment" }, width: { ideal: 4096 }, height: { ideal: 4096 } },
};

// 1080p (1920) is within 6% of the 2048 kept; 1024 is where the model was
// measured to stop reading small text. Below 1920, say so.
const ENOUGH_EDGE = 1920;

export function streamQuality(width, height) {
  const w = Number(width) || 0;
  const h = Number(height) || 0;
  const edge = Math.max(w, h);
  if (!edge) return null;
  return { edge, enough: edge >= ENOUGH_EDGE, size: `${w} × ${h}` };
}

// What the server keeps (storage.kept_size): 2048 on the short edge, never
// over 4096 on the long; never up. null before the video has dimensions.
export function frameSize(width, height) {
  const w = Number(width);
  const h = Number(height);
  if (!(w > 0) || !(h > 0)) return null;
  const scale = Math.min(1, 2048 / Math.min(w, h), 4096 / Math.max(w, h));
  return { width: Math.round(w * scale), height: Math.round(h * scale) };
}


// --- which parts of it show -------------------------------------------------
//
// `live` is a stream running, `shown` a photograph on screen. One rule, so a
// dialog and a page cannot end up showing a shutter over a still, or offering
// "Take another" for a file chosen from the picker.
export function viewfinderState({ live = false, shown = false } = {}) {
  return {
    box: live || shown,
    cam: live && !shown,
    still: shown,
    // The shutter row is the camera's; a chosen file has nothing to retake.
    shots: live,
    shutter: live && !shown,
    retake: live && shown,
    // Nothing running: the way to a viewfinder. A page does not ask for a
    // camera nobody has allowed yet, so this is how it is asked for.
    start: !live,
  };
}

// --- what the line under it says --------------------------------------------
//
// `hint` is what this place calls a photo when there is not one yet -- the
// only words that differ between the dialog and the form. `made` is a record
// that already exists (the sheet's camera): a photo of one is not waiting on
// anything.
const POINT = "Point it at what is going in, then take the photo.";

export function photoLine({ state, hint = "", quality = null, name = "", made = false } = {}) {
  switch (state) {
    case "starting":
      return "Starting the camera\u2026";
    case "live":
      if (!quality) return POINT;
      if (quality.enough) return `${POINT} Camera: ${quality.size}.`;
      return `${POINT} This camera only gives ${quality.size}, so small labels may not be `
        + "readable. For detail, tap Choose a photo and use the phone's own camera.";
    case "shot":
      return made
        ? "This is the photo. Add it, and it is read in the background."
        : "This is the photo. It is read in the background once the record exists.";
    case "chosen":
      return `Photo: ${name || "chosen"}. `
        + (made ? "Add it, and it is read in the background." : "It will be read once the record exists.");
    default:
      return hint;
  }
}
