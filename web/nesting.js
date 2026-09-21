// Things inside things, as the page sees them: a bag in a box in a crate.
//
// The server keeps the pointer honest (a parent must exist, be a container, not
// be binned, and not be inside the thing being moved) and says why when it
// refuses. These are the same rules as the page knows them, decided before the
// server is asked, so that a move is never offered that would be refused and
// the reason is on the screen at once. The wording for what is inside a row
// lives in covers.js with the rest of the row; the way out of a nested record
// and the naming of a container live here. Pure: no DOM, tested under node.

// The word for a kind, as the picker shows it. Only for the two that need a
// longer name in a sentence; every other kind reads as its own key.
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

/**
 * Whether `container` may hold `record` -- `record`'s direct children are
 * `children`, and `kinds` is what /api/settings/kinds says holds contents.
 *
 * Deeper descendants are not known here (a child's own children are only a
 * count); the server refuses those, and its reason is shown the same way.
 */
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

/**
 * Where a nested record goes: wherever its nearest container with a room goes.
 * `path` is outermost first, so the search runs from the end. Returns
 * `{ code, room }` -- the container whose room it is, or the nearest one with
 * `room: null` when none has chosen -- or null at the top level.
 */
export function inheritedRoom(path) {
  const steps = path || [];
  if (!steps.length) return null;
  const chosen = [...steps].reverse().find((step) => step.destination_room_id != null);
  const step = chosen || steps[steps.length - 1];
  return { code: step.code, room: chosen ? chosen.destination_room_id : null };
}

/**
 * The containers a fragile record is inside that are not themselves marked
 * fragile, outermost first: the ones the page offers to mark. Fragile climbs;
 * it never descends, and clearing it never climbs.
 */
export function notYetFragile(path) {
  return (path || []).filter((step) => !step.fragile);
}

// --- adding something inside, from the container's page --------------------------
//
// "Adding a subitem should pop up a dialog that asks for type and a photo and
// an optional source. The rest of the activities can be done from the ui."

/** The kinds the dialog offers: all of them. A crate can hold a bag or a lamp. */
export function kindsToAddInside(kinds) {
  return [...(kinds || [])];
}

/**
 * What the dialog sends to make the record: the kind, the container, and the
 * source room or null. No summary (the photo names the contents), no
 * destination (it goes where the container goes), no size, no flags.
 */
export function addInsideRequest({ kind, parentCode, sourceRoom }) {
  return { kind, parent_code: parentCode, source_room_id: sourceRoom ? Number(sourceRoom) : null };
}

/**
 * What to say once it is made. A photo that did not upload is said, not
 * dropped: the record stands, and there is a page to add one from.
 */
export function addedInside(made, photoError) {
  const named = `Added ${made.code} (${spoken(made.kind)})`;
  if (!photoError) return { text: `${named}.`, warn: false };
  return {
    text: `${named}, but its photo did not upload: ${photoError.message}. Add one from its page.`,
    warn: true,
  };
}

// --- the viewfinder in the add dialog ----------------------------------------------
//
// "can we use javascript to have a live camera immediately during adding a
// subitem?" The camera is asked for when the dialog opens, and it can fail in
// half a dozen ordinary ways. None of them is an error state: the file picker
// is still there, and the line says which of them happened. (The insecure case
// is checked *before* asking, because over a plain LAN address getUserMedia
// rejects with nothing useful -- see the HTTPS note in CLAUDE.md.)
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

/**
 * What a captured frame is drawn at: its own size, down to `limit` on the long
 * edge. The server downscales to 2048 and strips the metadata anyway, so
 * anything larger is a phone pushing a 4K frame through a house's wifi for
 * nothing -- and a frame is never blown up to meet it. null when the video has
 * no dimensions yet, which is how it is before it has data.
 */
export function frameSize(width, height, limit = 2048) {
  const w = Number(width);
  const h = Number(height);
  if (!(w > 0) || !(h > 0)) return null;
  const scale = Math.min(1, limit / Math.max(w, h));
  return { width: Math.round(w * scale), height: Math.round(h * scale) };
}

// --- the sub-item editor's sections -----------------------------------------------
//
// "pop open the subitem editor as a modal, rather than changing page. collapse
// unused inputs using >v style expand/collapse indicators."
//
// The rule is about content, not about which field it is: a section with
// something in it starts open, an empty one starts folded. **Nothing with
// content is ever folded away** -- otherwise somebody edits a record without
// seeing what is already on it. A section that does not apply at all (the size
// of a lamp, the contents of a lamp) is not there in the first place.
//
// Deciding it here rather than in the markup keeps it one rule to extend: the
// record page could fold the same way later without rewriting the reasoning.
const filled = (value) => String(value ?? "").trim() !== "";

export function editorSections(box, items, shape, photos) {
  const record = box || {};
  const holds = Boolean(shape?.contents);
  const sections = [
    { key: "summary", legend: holds ? "What is in it" : "What it is", open: filled(record.content_summary) },
    // A record is always something, so this one is always open: the rule
    // decides it, not an exception to the rule.
    { key: "kind", legend: "Kind", open: filled(record.kind) },
    ...(holds ? [{ key: "size", legend: "How big", open: filled(record.size) }] : []),
    { key: "source", legend: "Where it came from",
      open: filled(record.source_room_id) || filled(record.source_location) },
    { key: "handling", legend: "Handling",
      open: Boolean(record.fragile || record.heavy || record.open_first) },
    ...(holds ? [{ key: "items", legend: "Items", open: (items || []).length > 0 }] : []),
    // "show image thumbs and show the full view on demand". Unlike every
    // section above it this one is not an input, so there is nothing to fold
    // *open* to: a record with no photos has no photo section at all, the way
    // a lamp has no size. Photos are not about holding contents, so a single
    // thing has them too. Last, because the fields are what you came to fix
    // and the pictures are what you already have.
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
