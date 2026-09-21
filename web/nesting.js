// Things inside things, as the page sees them. The server enforces the same
// rules; deciding them here first means a move it would refuse is never
// offered. No DOM, so it is tested under node.

// Only kinds that need a longer name in a sentence.
const SPOKEN = { item: "loose item" };
const spoken = (kind) => SPOKEN[kind] || kind || "box";

/** A container in a sentence: "kitchen crate", or just "crate". */
export function describe(box) {
  const kind = spoken(box.kind);
  const summary = String(box.content_summary ?? "").trim();
  if (!summary) return kind;
  // "kitchen crate" is already the whole name.
  return summary.toLowerCase().endsWith(kind) ? summary : `${summary} ${kind}`;
}

/** The breadcrumb for a nested record: `path` outermost first, each step named. */
export function trail(path) {
  return (path || []).map((step) => ({ code: step.code, hint: describe(step) }));
}

// `children` are `record`'s direct children; deeper ones are unknown here, and
// the server's refusal is shown the same way.
export function mayHold(container, record, children, kinds) {
  if (!container) return { ok: false, why: "There is no record with that code." };
  const no = (why) => ({ ok: false, why });
  if (container.code === record.code) return no("It cannot go inside itself.");
  if ((children || []).some((child) => child.code === container.code)) {
    return no(`${container.code} is inside this one, so this one cannot go inside it.`);
  }
  const shape = (kinds || []).find((k) => k.kind === container.kind);
  if (shape && !shape.contents) {
    return no(`${container.code} is a ${spoken(container.kind)}, not a container.`);
  }
  if (container.deleted_at) return no(`${container.code} is in the bin.`);
  return { ok: true, why: "" };
}

// Where a nested record goes: wherever its nearest container with a room goes.
// Returns `{ code, room }` -- the container whose room it is, or the nearest
// one with `room: null` when none has chosen -- or null at the top level.
export function inheritedRoom(path) {
  const steps = path || [];
  if (!steps.length) return null;
  const chosen = [...steps].reverse().find((step) => step.destination_room_id != null);
  const step = chosen || steps[steps.length - 1];
  return { code: step.code, room: chosen ? chosen.destination_room_id : null };
}

// The containers not yet fragile, outermost first: the ones the page offers to
// mark. Fragile climbs, never descends, and clearing it never climbs.
export function notYetFragile(path) {
  return (path || []).filter((step) => !step.fragile);
}

// --- adding something inside, from the container's page --------------------------

// All of them: a crate can hold a bag or a lamp.
export function kindsToAddInside(kinds) {
  return [...(kinds || [])];
}

// No summary (the photo names the contents), no destination (it follows the
// container), no size, no flags.
export function addInsideRequest({ kind, parentCode, sourceRoom }) {
  return { kind, parent_code: parentCode, source_room_id: sourceRoom ? Number(sourceRoom) : null };
}

// A photo that did not upload is said, not dropped: the record stands.
export function addedInside(made, photoError) {
  const named = `Added ${made.code} (${spoken(made.kind)})`;
  if (!photoError) return { text: `${named}.`, warn: false };
  return {
    text: `${named}, but its photo did not upload: ${photoError.message}. Add one from its page.`,
    warn: true,
  };
}

// --- search results, read as a tree ------------------------------------------------
//
// Each container first, its matches indented under it. The server sends the
// matches and their containers, each row carrying `matched` and its `ancestry`
// (codes, outermost first). No match may be hidden, dropped, or shown twice
// when both a record and its container matched.
export function groupMatches(rows) {
  const all = rows || [];
  const byCode = new Map(all.map((row) => [row.code, row]));
  // Only ancestors in the results count, so depth is the steps *shown*.
  const chainOf = (row) => (row.ancestry || []).filter((code) => byCode.has(code));

  const out = [];
  const placed = new Set();
  const place = (row) => {
    // Marked before its containers are walked, so a cycle in the data stops.
    if (!row || placed.has(row.code)) return;
    placed.add(row.code);
    for (const code of chainOf(row)) place(byCode.get(code));
    // The row's own flag, not how it was placed: a container that matched and
    // holds a match is a match. `=== false`, so a list that flags nothing
    // (browsing, a container's contents) is all matches.
    out.push({ row, depth: chainOf(row).length, context: row.matched === false });
  };
  // Search order, so the best match leads; a group takes its first match's place.
  for (const row of all) place(row);
  return out;
}

// --- the viewfinder in the add dialog ----------------------------------------------
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

// --- the sub-item editor's sections -----------------------------------------------
//
// A section with something in it starts open, an empty one folded. Nothing
// with content is ever folded, or a record gets edited without seeing what is
// on it. A section that does not apply (a lamp's size) is absent.
const filled = (value) => String(value ?? "").trim() !== "";

export function editorSections(box, items, shape, photos) {
  const record = box || {};
  const holds = Boolean(shape?.contents);
  const sections = [
    { key: "summary", legend: holds ? "What is in it" : "What it is", open: filled(record.content_summary) },
    { key: "kind", legend: "Kind", open: filled(record.kind) },
    ...(holds ? [{ key: "size", legend: "How big", open: filled(record.size) }] : []),
    { key: "source", legend: "Where it came from",
      open: filled(record.source_room_id) || filled(record.source_location) },
    { key: "handling", legend: "Handling",
      open: Boolean(record.fragile || record.heavy || record.open_first) },
    ...(holds ? [{ key: "items", legend: "Items", open: (items || []).length > 0 }] : []),
    // Not an input, so nothing to fold open to: no photos, no section.
    ...((photos || []).length ? [{ key: "photos", legend: "Photos", open: true }] : []),
  ];
  return sections;
}

/** Why Delete is not offered on a container holding things, or "" when it is. */
export function blockedDelete(children) {
  const count = (children || []).length;
  if (!count) return "";
  return count === 1
    ? "Move the thing inside it out first."
    : `Move the ${count} things inside it out first.`;
}
