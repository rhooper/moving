// Pure helpers for the list rows and the photo strip, kept out of app.js
// (which touches the DOM on import) so they can be tested. A list row gets its
// cover id from the server and the box page works it out from its photos; both
// go through here so they cannot drift apart.

// The ~400px thumb, never /full. "" when there is no photo: an empty src
// re-requests the page itself.
export function thumbUrl(photoId) {
  if (photoId === null || photoId === undefined || photoId === "") return "";
  return `/photos/${encodeURIComponent(photoId)}/thumb`;
}

export function coverUrl(box) {
  return thumbUrl(box && box.cover_photo_id);
}

// The schema keeps exactly one photo flagged; the first-photo fallback only
// guards against that slipping.
export function coverOf(photos) {
  const list = photos || [];
  return list.find((photo) => photo.is_primary) || list[0] || null;
}

// A strip figure's drawn width, for `<img sizes>`: half of <main>'s content box
// (at most 34rem, 1rem padding each side) less half the gap. The sub-item
// modal's strip is a little narrower, so it over-asks there: the safe direction.
export const STRIP_SIZES = "(min-width: 34rem) 15.75rem, calc(50vw - 1.25rem)";

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
// Each photo carries a snapshot of its reading job; this turns the snapshot and
// the time since it arrived into what the strip draws.

// A closer look is slower, and says so, so it is not taken for a stalled read.
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

// What to draw `elapsedMs` after `analysis` (the photo's, or null) arrived:
//
// - `state`: "none" | "pending" | "running" | "done" | "error"
// - `busy`: whether the ring shows at all
// - `fraction`: how much of the ring is filled, below 1; null with no honest number
// - `indeterminate`: spin instead of fill, once the estimate has run out: a
//   full ring would say "done" when the truth is "no idea"
// - `label`: the line under the photo; `title`: an error in full
// - `retry`: whether to offer another go
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
      // Time left but no span to measure it against: still show the seconds.
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

// Rounded up, so the last second reads "~1 s" while the ring still moves.
function countdown(status, remainingMs, closer = false) {
  const words = closer ? CLOSER_WORDS : WORDS;
  return `${words[status]}… ~${Math.ceil(remainingMs / 1000)} s`;
}

// A list row's last cell: the kind, size first ("large box"), over the status,
// and "3 inside" for a container. One function for the first draw and for live
// updates, so the two cannot disagree. A nested record shows no status: it goes
// where its container goes. The cell does not wrap, so "extra large" is XL.
const SHORT_SIZE = { "extra large": "XL" };

export function rowStatus(box) {
  const kind = box.kind || "box";
  const size = box.size ? (SHORT_SIZE[box.size] || box.size) : "";
  const count = Number(box.child_count) || 0;
  const nested = Boolean(box.parent_code);
  return {
    kind: size ? `${size} ${kind}` : kind,
    status: nested ? "" : (box.status || "open"),
    inside: count ? `${count} inside` : "",
  };
}

// --- which mark stands for a thing -------------------------------------
//
// A <use> at a symbol the sprite lacks draws nothing and says nothing, so the
// mappings from data live here, tested, and an unknown kind falls back to a
// mark that exists.

const KIND_ICONS = {
  box: "i-box",
  parts: "i-parts",
  tub: "i-tub",
  crate: "i-crate",
  bag: "i-bag",
  item: "i-item",
  furniture: "i-furniture",
};

export function kindIcon(box) {
  // The same fallback rowStatus makes, so the mark and the word agree.
  return KIND_ICONS[(box && box.kind) || "box"] || KIND_ICONS.box;
}

const FLAG_ICONS = {
  fragile: "i-fragile",
  heavy: "i-heavy",
  open_first: "i-open-first",
};

export function flagIcon(flag) {
  return FLAG_ICONS[flag] || "";
}

// What the model saw in *this* photo, not the record's merged contents, so a
// wrong item can be traced to the photo it came from.
export function seenIn(analysis, { readable = true } = {}) {
  // `closer`: "offer" once a quick read is in, even one that found nothing;
  // "done" after a closer look; null while running, failed or never read.
  // `rerun`: the ordinary read's button label, or null while a read is out
  // and for a single thing, whose photos are never read.
  // `by`: the model that actually read it; a fallback may have stood in.
  const base = {
    state: "none", heading: "Seen in this photo", summary: "", items: [], note: "",
    closer: null, rerun: null, by: "",
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
  // A reply without a list shows nothing rather than breaking.
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
    by: analysis.model ? `Read by ${analysis.model}` : "",
  };
}

// --- what reading photos costs --------------------------------------------

// Never a flat "$0.00" while money has been spent.
export function money(amount) {
  const value = Number(amount) || 0;
  if (value > 0 && value < 0.01) return "less than $0.01";
  return `$${value.toFixed(2)}`;
}

// The Settings panel's first line answers "is the good model reading my photos
// now?"; when it is not, the next says why and what to do.
export function readingWith(spend) {
  const s = spend || {};
  const local = s.local_model || "the local model";
  const spent = Number(s.spent_usd) || 0;
  const cap = Number(s.cap_usd) || 0;
  const bar = {
    spent: money(spent),
    cap: money(cap),
    // Clamped: over the cap the bar is full, not overflowing.
    fraction: cap > 0 ? Math.min(1, spent / cap) : 1,
  };

  if (s.provider !== "claude") {
    return {
      ...bar, state: "local",
      now: `Photos are read on this machine, by ${local}.`,
      why: "Nothing is spent. Set MOVING_VISION_PROVIDER=claude to use the cloud tier.",
    };
  }
  // `key` says only whether one is set: the value never reaches the browser.
  if (!s.key) {
    return {
      ...bar, state: "nokey",
      now: `Photos are read on this machine, by ${local}.`,
      why: "There is no API key, so nothing is spent. Put ANTHROPIC_API_KEY in .env and restart.",
    };
  }
  if (s.over) {
    return {
      ...bar, state: "over",
      now: `The budget is spent, so photos are read on this machine, by ${local}.`,
      why: `Raise MOVING_VISION_BUDGET_USD above ${money(spent)} and restart to carry on.`,
    };
  }
  // Cloud configured with a key, yet every recent read was local: a refused key
  // or nothing answering. Otherwise it shows only in a log.
  if (s.recent_reads && s.recent_local === s.recent_reads) {
    return {
      ...bar, state: "failing",
      now: `${s.model} is configured, but the last ${s.recent_reads}
            ${s.recent_reads === 1 ? "photo was" : "photos were"} read on this machine by ${local}.`,
      why: "The key may have been refused, or nothing answered. See var/log/moving.err.log.",
    };
  }
  return {
    ...bar, state: "cloud",
    now: `Photos are read by ${s.model}.`,
    why: `Look closer uses ${s.detail_model}. If either cannot be reached, ${local} reads it instead.`,
  };
}
