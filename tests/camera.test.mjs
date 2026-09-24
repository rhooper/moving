// The live camera, away from the DOM: what it asks the browser for, what a
// frame comes out as, what every ordinary failure reads like, and -- now that
// two pages share one camera -- which of its parts show and what the line
// under it says. Two copies of that would be two cameras drifting apart.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CAMERA_REQUEST,
  cameraTrouble,
  frameSize,
  photoLine,
  streamQuality,
  viewfinderState,
} from "../web/camera.js";

// --- what it asks for ----------------------------------------------------------

test("the live camera asks for a size, not only for the back camera", () => {
  // With no size asked for, the browser hands back its default 640x480.
  const video = CAMERA_REQUEST.video;
  assert.deepEqual(video.facingMode, { ideal: "environment" });
  assert.ok(video.width.ideal >= 2048 && video.height.ideal >= 2048);
});

test("a stream says what size it is, and whether it is enough", () => {
  assert.deepEqual(streamQuality(3840, 2160), { edge: 3840, enough: true, size: "3840 × 2160" });
  assert.deepEqual(streamQuality(1920, 1080), { edge: 1920, enough: true, size: "1920 × 1080" });
  assert.deepEqual(streamQuality(1080, 1920), { edge: 1920, enough: true, size: "1080 × 1920" });
  assert.equal(streamQuality(1280, 720).enough, false);
  assert.equal(streamQuality(640, 480).enough, false);
});

test("a stream with no dimensions yet says nothing", () => {
  assert.equal(streamQuality(0, 0), null);
  assert.equal(streamQuality(undefined, undefined), null);
});

// --- what a captured frame comes out as ----------------------------------------
//
// The server keeps 2048 px at most, so there is no point uploading more.

test("a big frame comes down to what the server keeps: 2048 on the short edge", () => {
  assert.deepEqual(frameSize(4032, 3024), { width: 2731, height: 2048 });
  // Held upright, the short edge is the width.
  assert.deepEqual(frameSize(3024, 4032), { width: 2048, height: 2731 });
});

test("a very wide frame is held to 4096 on its long edge", () => {
  assert.deepEqual(frameSize(8000, 1000), { width: 4096, height: 512 });
});

test("a small frame is left alone rather than blown up", () => {
  assert.deepEqual(frameSize(640, 480), { width: 640, height: 480 });
  assert.deepEqual(frameSize(1080, 1920), { width: 1080, height: 1920 });
  assert.deepEqual(frameSize(2048, 1536), { width: 2048, height: 1536 });
});

test("the shape is kept, to whole pixels", () => {
  const { width, height } = frameSize(3500, 2333);
  assert.equal(height, 2048);
  assert.equal(width, Math.round(3500 * (2048 / 2333)));
  assert.ok(Number.isInteger(width));
});

test("a frame with no size yet is not a frame", () => {
  // The video element has no dimensions until it has data.
  for (const bad of [[0, 0], [640, 0], [Number.NaN, 480]]) {
    assert.equal(frameSize(...bad), null);
  }
});

// --- every way it can fail is ordinary -----------------------------------------
//
// No permission, no camera, a plain LAN address: none of them is an error
// state. The file picker is still there, and the line says which happened.

test("no secure context is the one that is about the address, not the camera", () => {
  // getUserMedia rejects silently on a LAN IP; this is checked before asking.
  const said = cameraTrouble(null, { secure: false });
  assert.match(said, /secure connection/);
  assert.match(said, /[Cc]hoose a photo/);
});

test("a refusal says so plainly, and is not an error", () => {
  const said = cameraTrouble({ name: "NotAllowedError" });
  assert.match(said, /declined/);
  assert.match(said, /[Cc]hoose a photo/);
  assert.doesNotMatch(said, /error|failed/i);
});

test("no camera, and a camera somebody else is using, read differently", () => {
  assert.match(cameraTrouble({ name: "NotFoundError" }), /No camera/);
  assert.match(cameraTrouble({ name: "OverconstrainedError" }), /No camera/);
  assert.match(cameraTrouble({ name: "NotReadableError" }), /already in use|busy/i);
});

test("anything else names itself rather than pretending to know", () => {
  const said = cameraTrouble({ name: "AbortError" });
  assert.match(said, /AbortError/);
  assert.match(said, /[Cc]hoose a photo/);
});

test("every one of them points at the way that still works", () => {
  for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "AbortError", undefined]) {
    assert.match(cameraTrouble(name ? { name } : null), /[Cc]hoose a photo/);
  }
});

// --- which parts of it show ----------------------------------------------------
//
// One rule for both places it is used, so a dialog and a page cannot end up
// showing a shutter over a still or a Take another with nothing to retake.

test("with no camera and no photo there is nothing to show", () => {
  assert.deepEqual(viewfinderState({ live: false, shown: false }), {
    box: false, cam: false, still: false, shots: false, shutter: false, retake: false, start: true,
  });
});

test("a running camera is a viewfinder and a shutter", () => {
  assert.deepEqual(viewfinderState({ live: true, shown: false }), {
    box: true, cam: true, still: false, shots: true, shutter: true, retake: false, start: false,
  });
});

test("a frame taken shows the frame, and offers another", () => {
  assert.deepEqual(viewfinderState({ live: true, shown: true }), {
    box: true, cam: false, still: true, shots: true, shutter: false, retake: true, start: false,
  });
});

test("a photo chosen from the picker has nothing to retake", () => {
  // No stream: the camera was refused, or never asked for, and the file picker
  // is what supplied the photo. Offering "Take another" would do nothing.
  assert.deepEqual(viewfinderState({ live: false, shown: true }), {
    box: true, cam: false, still: true, shots: false, shutter: false, retake: false, start: true,
  });
});

test("with no camera running there is a way to start one", () => {
  // The page does not ask for a camera nobody has allowed yet, so the button
  // is how it is asked for -- and it is gone the moment there is a viewfinder
  // or a photograph.
  assert.equal(viewfinderState({ live: false, shown: false }).start, true);
  assert.equal(viewfinderState({ live: true, shown: false }).start, false);
  assert.equal(viewfinderState({ live: true, shown: true }).start, false);
});

test("a photo chosen from the picker still leaves the camera offered", () => {
  // Nothing is running, so the way to a viewfinder is still worth showing.
  assert.equal(viewfinderState({ live: false, shown: true }).start, true);
});

test("a camera that failed leaves no dead grey rectangle", () => {
  // cameraTrouble says what happened; the box is put away rather than left empty.
  assert.equal(viewfinderState({ live: false, shown: false }).box, false);
  assert.equal(viewfinderState({}).box, false);
});

// --- what the line under it says -----------------------------------------------

test("with nothing yet, the line is whatever this place calls a photo", () => {
  // The dialog and the new-record form each say what a photo is for there;
  // everything else about the camera reads the same in both.
  assert.equal(photoLine({ state: "none", hint: "No photo yet, and none is needed." }),
               "No photo yet, and none is needed.");
});

test("starting says so, so a slow camera does not look broken", () => {
  assert.match(photoLine({ state: "starting" }), /Starting the camera/);
});

test("a live viewfinder says what to do with it", () => {
  const said = photoLine({ state: "live" });
  assert.match(said, /Point it at/);
  assert.doesNotMatch(said, /Camera:/);
});

test("a good camera says what size it is; a poor one says what that costs", () => {
  const good = photoLine({ state: "live", quality: streamQuality(1920, 1080) });
  assert.match(good, /Camera: 1920 × 1080/);
  assert.doesNotMatch(good, /small labels/);

  const poor = photoLine({ state: "live", quality: streamQuality(640, 480) });
  assert.match(poor, /640 × 480/);
  assert.match(poor, /small labels/);
  // And it points at the way to get a better one.
  assert.match(poor, /Choose a photo/);
});

test("a frame taken says it is the photo, and what will happen to it", () => {
  const said = photoLine({ state: "shot" });
  assert.match(said, /This is the photo/);
  assert.match(said, /read/);
});

test("a chosen file is named, so it is obvious which one it is", () => {
  const said = photoLine({ state: "chosen", name: "IMG_4021.HEIC" });
  assert.match(said, /IMG_4021\.HEIC/);
});

test("a chosen file with no name still reads as a sentence", () => {
  assert.match(photoLine({ state: "chosen" }), /Photo/);
  assert.doesNotMatch(photoLine({ state: "chosen" }), /undefined|null/);
});

test("a photo of a record that already exists does not wait on one being made", () => {
  // The sheet's camera photographs a record that is already there: "once the
  // record exists" would be a promise about the past.
  const shot = photoLine({ state: "shot", made: true });
  assert.match(shot, /This is the photo/);
  assert.doesNotMatch(shot, /record exists/);
  assert.match(shot, /read/);
  const chosen = photoLine({ state: "chosen", name: "IMG_4021.HEIC", made: true });
  assert.match(chosen, /IMG_4021\.HEIC/);
  assert.doesNotMatch(chosen, /record exists/);
  assert.match(chosen, /read/);
});

test("an unknown state says nothing rather than something wrong", () => {
  assert.equal(photoLine({ state: "sideways", hint: "nothing yet" }), "nothing yet");
  assert.equal(photoLine({}), "");
});
