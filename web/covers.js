// Which photo stands for a box, and where its thumbnail comes from.
//
// Kept out of app.js so it can be tested directly -- app.js touches the DOM at
// import time and cannot be loaded in isolation. Same arrangement as text.js.
//
// The list and the box page reach the same answer by different routes: a list
// row is told the cover id by the server (so a screenful of boxes stays one
// request), while the box page already has the whole photo list and works it
// out from the flags. Both end up here so they cannot drift apart.

/**
 * The URL of a photo's thumbnail.
 *
 * Always the ~400px thumb, never `/full`. A hundred-box list rendering 2048px
 * originals would be tens of megabytes over a phone's connection to draw
 * pictures the size of a postage stamp.
 *
 * Returns "" when there is no photo: an `<img src="">` re-requests the page
 * itself in some browsers, and `/photos/null/thumb` is a guaranteed 404 on
 * every row that has no picture.
 */
export function thumbUrl(photoId) {
  if (photoId === null || photoId === undefined || photoId === "") return "";
  return `/photos/${encodeURIComponent(photoId)}/thumb`;
}

/** The cover thumbnail for a box list row, from the id the list carried. */
export function coverUrl(box) {
  return thumbUrl(box && box.cover_photo_id);
}

/**
 * The photo a box is recognised by, given its photos.
 *
 * The server keeps exactly one flagged (the schema enforces it), so the
 * fallback to the first photo is only this side refusing to draw a strip with
 * no cover marked at all if that ever slips.
 */
export function coverOf(photos) {
  const list = photos || [];
  return list.find((photo) => photo.is_primary) || list[0] || null;
}

/** The box page's photo strip: every photo, its thumbnail, and which is cover. */
export function stripFor(photos) {
  const cover = coverOf(photos);
  return (photos || []).map((photo) => ({
    ...photo,
    thumb: thumbUrl(photo.id),
    cover: cover !== null && photo.id === cover.id,
  }));
}

// --- a photo's analysis ---------------------------------------------------
//
// After an upload the server reads the photo with the vision model in the
// background. Each photo carries a snapshot of that job; this turns a snapshot
// plus the time since it arrived into what the strip should draw. Pure, so
// the awkward moments -- the estimate running out before the job does, an
// estimate that makes no sense -- are pinned down by tests rather than by
// staring at a spinner.

// A closer look is the slower, more careful model, run only when asked for;
// it says so, so a ten-second wait is not mistaken for the quick read stalling.
const WORDS = { pending: "Queued", running: "Reading" };
const STILL = { pending: "Still queued…", running: "Still reading…" };
const CLOSER_WORDS = { pending: "Queued", running: "Looking closer" };
const CLOSER_STILL = { pending: "Still queued…", running: "Still looking…" };
const ERROR_LENGTH = 60;

const NO_ANALYSIS = Object.freeze({
  state: "none", busy: false, indeterminate: false, fraction: null,
  label: "", title: "", retry: false,
});

function shorten(text, length) {
  return text.length <= length ? text : `${text.slice(0, length - 1).trimEnd()}…`;
}

/**
 * What to draw for a photo's analysis, `elapsedMs` after `analysis` arrived.
 *
 * `analysis` is the object the API puts on each photo (or null). The result:
 *
 * - `state`: "none" | "pending" | "running" | "done" | "error"
 * - `busy`: whether the ring shows at all
 * - `fraction`: how much of the ring is filled, 0 up to but never reaching 1;
 *   null whenever there is no honest number
 * - `indeterminate`: the ring should spin instead of fill. True once the
 *   estimate has run out and the job has not finished: a ring sitting full
 *   says "done" when the truth is "no idea", so there is deliberately no
 *   fraction to draw in that state.
 * - `label`: the line of text under the photo; `title`: an error in full
 * - `retry`: whether to offer another go
 */
export function analysisView(analysis, elapsedMs = 0) {
  const status = analysis && analysis.status;

  if (status === "done") {
    const found = analysis.items_found;
    const label =
      typeof found !== "number" ? "Photo read"
      : found === 0 ? "Nothing recognised"
      : `${found} item${found === 1 ? "" : "s"} found`;
    return { ...NO_ANALYSIS, state: "done", label };
  }

  if (status === "error") {
    const said = String(analysis.error ?? "").trim() || "Could not read this photo";
    return {
      ...NO_ANALYSIS, state: "error", retry: true,
      label: shorten(said, ERROR_LENGTH), title: said,
    };
  }

  if (status !== "pending" && status !== "running") return NO_ANALYSIS;

  const elapsed = Math.max(0, Number(elapsedMs) || 0);
  const remaining = (Number(analysis.remaining_ms) || 0) - elapsed;
  const total = Number(analysis.total_ms) || 0;
  if (remaining <= 0 || total <= 0) {
    return {
      ...NO_ANALYSIS, state: status, busy: true, indeterminate: true,
      // With time still on the clock but no span to measure it against, the
      // seconds are still worth showing; past zero there is nothing to count.
      label: remaining > 0
        ? countdown(status, remaining, analysis.detail)
        : (analysis.detail ? CLOSER_STILL : STILL)[status],
    };
  }
  return {
    ...NO_ANALYSIS, state: status, busy: true,
    fraction: Math.min(Math.max(1 - remaining / total, 0), 0.999),
    label: countdown(status, remaining, analysis.detail),
  };
}

// Rounded up, so the last second reads "~1 s" rather than "~0 s" while the
// ring is visibly still moving.
function countdown(status, remainingMs, closer = false) {
  const words = closer ? CLOSER_WORDS : WORDS;
  return `${words[status]}… ~${Math.ceil(remainingMs / 1000)} s`;
}

// What a list row says about a record beside its summary: what it is, over
// how far along it is. One function, because the first draw and the live
// update once disagreed about this cell -- the draw showed the kind, the
// update overwrote it with a location -- and nobody noticed until it was read.
//
// Deliberately not the current location, which this cell used to show when
// there was one: it hid the status, and it has the whole record page.
//
// A container's size goes in front of what it is -- "large box" is how people
// look for one. The cell is narrow and does not wrap, so "extra large" is XL.
const SHORT_SIZE = { "extra large": "XL" };

export function rowStatus(box) {
  const kind = box.kind || "box";
  const size = box.size ? (SHORT_SIZE[box.size] || box.size) : "";
  return { kind: size ? `${size} ${kind}` : kind, status: box.status || "open" };
}

// What the photo viewer says beside a picture: what the model saw in *this*
// photo. Not the record's contents list, which is merged from every photo and
// from whatever people typed -- this is the evidence for one picture, so a
// wrong item can be traced to the photo it came from.
export function seenIn(analysis, { readable = true } = {}) {
  // `closer`: "offer" once a quick read is in -- including one that found
  // nothing, which is exactly when you want it; "done" after a closer look;
  // null while anything is running, and for a photo that failed (a second
  // model will not reach a server the first could not) or was never read.
  //
  // `rerun`: what the button that runs the ordinary read should say, or null
  // when it should not be there -- while a read is queued or running (it would
  // only queue a second behind the first), and on a record that is a single
  // thing, whose photos are never read and which the server would refuse.
  const base = {
    state: "none", heading: "Seen in this photo", summary: "", items: [], note: "",
    closer: null, rerun: null,
  };
  if (!readable) {
    return {
      ...base,
      note: "Photos of a single thing are not read: there are no contents to list.",
    };
  }
  if (!analysis) {
    return { ...base, note: "This photo has not been read.", rerun: "Read this photo" };
  }
  if (analysis.status === "pending" || analysis.status === "running") {
    return {
      ...base,
      state: "busy",
      note: analysis.detail
        ? "Taking a closer look at this photo. It is slower than the first read."
        : "This photo is still being read.",
    };
  }
  if (analysis.status === "error") {
    return {
      ...base,
      state: "error",
      note: `It could not be read: ${analysis.error || "no reason given"}`,
      rerun: "Try again",
    };
  }
  // An older server sends a count and no list; show nothing rather than break.
  const items = Array.isArray(analysis.items) ? analysis.items : [];
  return {
    ...base,
    state: "done",
    heading: analysis.detail ? "Seen on a closer look" : base.heading,
    summary: analysis.summary || "",
    items,
    note: items.length ? "" : "Nothing was recognised in this photo.",
    closer: analysis.detail ? "done" : "offer",
    rerun: "Read again",
  };
}
