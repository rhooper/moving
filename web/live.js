// Live updates: the socket that says a box changed, and when it is safe to act
// on that. Nothing here reads `document` or `WebSocket` at import time, so the
// decisions are tested under node.

export const PING = "ping";
export const RESYNC = "resync";

// Kinds that change what the list draws. `label.printed` does not.
// `items.changed` does because search matches item text, and `photos.changed`
// because a row shows its cover photo.
const LIST_KINDS = new Set([
  "box.created",
  "box.updated",
  "box.deleted",
  "box.status",
  "box.location",
  "items.changed",
  "photos.changed",
]);

// How long the screen stays still after a tap: pointerdown through click, plus
// slack for a slow thumb.
export const SETTLE_MS = 600;

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 30000;
const JITTER = 0.25;

// The fallback when a socket will not stay up; lazy, since it costs battery.
const POLL_MS = 20000;
const FALLBACK_AFTER = 2;

// Doubling, capped so a phone waking on a new network reconnects promptly, and
// jittered so devices that lost wifi together do not all return together.
export function backoffDelay(attempt, options = {}) {
  const {
    base = BASE_DELAY_MS,
    cap = MAX_DELAY_MS,
    jitter = JITTER,
    random = Math.random,
  } = options;
  const flat = Math.min(cap, base * 2 ** attempt);
  return Math.round(flat * (1 - jitter + 2 * jitter * random()));
}

// `view` is `{ name, code, clientId }`. Views other than the list and a box
// page (the new-box form, the scanner) match nothing, so a form being filled in
// is never refreshed.
export function affects(event, view) {
  if (!event || typeof event.kind !== "string") return false;
  if (event.kind === PING) return false;
  // A backlog was dropped, so nobody knows what was missed.
  if (event.kind === RESYNC) return true;
  if (!view) return false;
  // This device's own change: it already redrew from the response, and
  // redrawing again discards half-typed text.
  if (event.origin && view.clientId && event.origin === view.clientId) return false;

  if (view.name === "list") return LIST_KINDS.has(event.kind);
  if (view.name === "box") return Boolean(event.code) && event.code === view.code;
  return false;
}

// `fields` are `{ value, initial, focused }`. A focused field counts even when
// unchanged: a redraw takes the focus, which shuts a phone's keyboard mid-word.
export function hasUnsavedEdits(fields) {
  return (fields || []).some((field) => field.focused === true) || isDirty(fields);
}

// Whether any field differs from what it held when drawn; focus alone is not an edit.
export function isDirty(fields) {
  return (fields || []).some(
    (field) => String(field.value ?? "") !== String(field.initial ?? "")
  );
}

// Whether a refresh must wait. A list re-rendered between pointerdown and click
// sends the tap to whichever box slid into that spot.
export function holdRefresh(state, now = Date.now()) {
  const {
    editing = false,
    pointerDown = false,
    lastTouch = 0,
    settleMs = SETTLE_MS,
  } = state || {};
  if (editing || pointerDown) return true;
  return lastTouch > 0 && now - lastTouch < settleMs;
}

// Events that change one self-contained part of a box page. Photo analysis
// reports through these while somebody may be typing on that page, so each is
// applied to its part in place rather than held for a whole-page redraw.
const PARTS = new Map([
  ["items.changed", "items"],
  ["photos.changed", "photos"],
  ["box.updated", "summary"],
]);

// null when only a whole refresh will do, as for a resync.
export function partOf(event) {
  return (event && PARTS.get(event.kind)) || null;
}

// What `box.updated` may change that the page draws in place: the summary and
// the nesting. `updated_at` moves on every write.
const SUMMARY_KEYS = new Set([
  "content_summary", "summary_source", "updated_at", "children", "path", "parent",
]);

// `box.updated` also covers flags, rooms and kind; if any moved, the caller
// refreshes the whole page. Every key is compared, not a list, so a field added
// to the page later cannot be forgotten here.
export function onlySummaryChanged(before, after) {
  if (!before || !after) return false;
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  for (const key of keys) {
    if (SUMMARY_KEYS.has(key)) continue;
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) return false;
  }
  return true;
}

// Brings `parent`'s children into line with `items`, reusing the element keyed
// (`data-key`) to each. Rebuilding the markup would recreate every row, so a tap
// that began on one would land on its replacement; reuse keeps the tap target,
// the scroll position and any gesture under way.
export function reconcile(parent, items, { key, create, update }) {
  const existing = new Map();
  for (const child of Array.from(parent.children)) existing.set(child.dataset.key, child);

  let previous = null;
  for (const item of items) {
    const id = String(key(item));
    let element = existing.get(id);
    if (element) {
      existing.delete(id);
    } else {
      element = create(item);
      // Keyed here rather than trusted to every `create`: an unkeyed row is
      // never found again, and each update would draw a second copy.
      element.dataset.key = id;
    }
    update(element, item);
    // A node already in place is left alone: re-inserting it counts as a move.
    const wanted = previous ? previous.nextSibling : parent.firstChild;
    if (wanted !== element) parent.insertBefore(element, wanted);
    previous = element;
  }
  for (const gone of existing.values()) gone.remove();
}

// The socket, with reconnection and a polling fallback. `onEvent` gets every
// message, heartbeats included (filtering is `affects`'s job); polling
// synthesises RESYNC, so callers have one path for "something may have changed".
export class LiveChannel {
  constructor({ url, onEvent, onOpen = () => {}, pollMs = POLL_MS, fallbackAfter = FALLBACK_AFTER }) {
    this.url = url;
    this.onEvent = onEvent;
    // A deploy drops the socket, so an open is the first sign of new code.
    this.onOpen = onOpen;
    this.pollMs = pollMs;
    this.fallbackAfter = fallbackAfter;
    this.failures = 0;
    this.socket = null;
    this.retryTimer = null;
    this.pollTimer = null;
    this.running = false;
    this.wake = this.wake.bind(this);
  }

  start() {
    if (this.running) return;
    this.running = true;
    // A phone wakes on a different network: stop waiting out a backoff.
    addEventListener("online", this.wake);
    document.addEventListener("visibilitychange", this.wake);
    this.connect();
  }

  stop() {
    this.running = false;
    removeEventListener("online", this.wake);
    document.removeEventListener("visibilitychange", this.wake);
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.stopPolling();
    this.drop();
  }

  wake() {
    if (!this.running || document.hidden) return;
    // What happened while hidden is unknown, and the socket may have died
    // without either end noticing.
    this.onEvent({ kind: RESYNC });
    if (!this.socket) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.failures = 0;
      this.connect();
    }
  }

  connect() {
    if (!this.running || this.socket) return;
    let socket;
    try {
      socket = new WebSocket(this.url());
    } catch {
      // A malformed URL, or a policy that forbids the connection outright.
      this.retryLater();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.failures = 0;
      this.stopPolling();
      this.onOpen();
    };
    socket.onmessage = (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch {
        return; // not ours, or truncated
      }
      this.onEvent(event);
    };
    // onerror is always followed by onclose, so reconnection lives in one place.
    socket.onclose = () => {
      this.socket = null;
      this.retryLater();
    };
  }

  drop() {
    if (!this.socket) return;
    this.socket.onclose = null;
    this.socket.onmessage = null;
    try {
      this.socket.close();
    } catch {
      /* already closing */
    }
    this.socket = null;
  }

  retryLater() {
    if (!this.running) return;
    this.failures += 1;
    if (this.failures >= this.fallbackAfter) this.startPolling();
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, backoffDelay(this.failures - 1));
  }

  startPolling() {
    // Some proxies silently drop an Upgrade, so the socket never connects.
    // Polling starts only once it has really failed, and stops when one connects.
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      if (!document.hidden) this.onEvent({ kind: RESYNC });
    }, this.pollMs);
  }

  stopPolling() {
    clearInterval(this.pollTimer);
    this.pollTimer = null;
  }
}
