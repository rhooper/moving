// Live updates: the socket that says a box changed, and the decisions about
// when it is safe to act on that. Kept out of app.js so the decisions can be
// tested directly -- app.js touches the DOM at import time and cannot be
// loaded in isolation (same reason as text.js).
//
// Nothing here reads `document` or `WebSocket` at import time, so this module
// loads under node.

export const PING = "ping";
export const RESYNC = "resync";

// Kinds that change a row the box list shows. `label.printed` and
// `photos.changed` only alter what a box *page* shows, so the list ignores
// them rather than refetching every box in the house when a label comes out.
// `items.changed` is in here because search matches on item text: a search
// result set can change when nothing about the box row itself did.
const LIST_KINDS = new Set([
  "box.created",
  "box.updated",
  "box.deleted",
  "box.status",
  "box.location",
  "items.changed",
  // A photo used to be invisible from the list, and was deliberately left out
  // of this set. The cover thumbnail put it on the row, so a picture arriving,
  // being deleted, or being swapped for another one now changes what the list
  // draws -- and without this the other phone keeps showing the old picture,
  // or an empty square, until something else happens to that box.
  "photos.changed",
]);

/** How long after a tap the screen stays still. Covers pointerdown through
 *  click, plus enough slack that a slow thumb is not fighting a redraw. */
export const SETTLE_MS = 600;

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 30000;
const JITTER = 0.25;

/** The fallback when a socket will not stay up. Deliberately lazy: this is
 *  the mode that costs battery. */
const POLL_MS = 20000;
const FALLBACK_AFTER = 2;

/**
 * How long to wait before reconnect attempt `attempt` (0-based).
 *
 * Doubling, capped, and jittered. The cap matters because a phone that wakes
 * in a new place must not sit out a minute of backoff before it reconnects;
 * the jitter matters because every device in the house loses wifi at the same
 * moment when the router reboots, and they should not all come back together.
 */
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

/**
 * Whether `event` is worth refetching for, given the view on screen.
 *
 * `view` is `{ name, code, clientId }`. Views that are neither the list nor a
 * box page -- the new-box form, the scanner -- match nothing, which is how
 * the form a person is filling in never gets pulled out from under them.
 */
export function affects(event, view) {
  if (!event || typeof event.kind !== "string") return false;
  if (event.kind === PING) return false;
  // The server gave up on queueing a backlog, so there is no knowing what was
  // missed. Everything refetches.
  if (event.kind === RESYNC) return true;
  if (!view) return false;
  // This device's own change, come back to it. It already redrew from the
  // response it got; redrawing again is what discards half-typed text.
  if (event.origin && view.clientId && event.origin === view.clientId) return false;

  if (view.name === "list") return LIST_KINDS.has(event.kind);
  if (view.name === "box") return Boolean(event.code) && event.code === view.code;
  return false;
}

/**
 * Whether any field is mid-edit. `fields` are `{ value, initial, focused }`.
 *
 * A focused field counts even when its value still matches what was drawn:
 * redrawing takes the focus with it, which on a phone shuts the keyboard
 * mid-word.
 */
export function hasUnsavedEdits(fields) {
  return (fields || []).some(
    (field) =>
      field.focused === true || String(field.value ?? "") !== String(field.initial ?? "")
  );
}

/**
 * Whether a refresh must wait.
 *
 * The tap case is the one that actually bites: a list that re-renders between
 * pointerdown and click sends the tap to whichever box slid into that spot,
 * and you open the wrong box while looking at the right one.
 */
export function holdRefresh(state, now = Date.now()) {
  const {
    editing = false,
    drafting = false,
    pointerDown = false,
    lastTouch = 0,
    settleMs = SETTLE_MS,
  } = state || {};
  if (editing || drafting || pointerDown) return true;
  return lastTouch > 0 && now - lastTouch < settleMs;
}

/**
 * Bring `parent`'s children into line with `items`, reusing the element that
 * already stands for each key.
 *
 * This is the other half of not moving things under a thumb. Rebuilding a
 * list's markup destroys and recreates every row, so a tap that began on one
 * of them lands on whatever node took its place -- you open the wrong box
 * while looking at the right one. Reusing the node keeps the tap target, the
 * scroll position, and any gesture the browser is midway through.
 *
 * `create(item)` makes a new element, `update(element, item)` refreshes one.
 * Written against the DOM's own insertBefore/firstChild/nextSibling, so it is
 * exercised in tests against a stand-in for those four things.
 */
export function reconcile(parent, items, { key, create, update }) {
  const existing = new Map();
  for (const child of Array.from(parent.children)) existing.set(child.dataset.key, child);

  let previous = null;
  for (const item of items) {
    const id = String(key(item));
    let element = existing.get(id);
    if (element) existing.delete(id);
    else element = create(item);
    update(element, item);
    // Already in the right place? Then leave it completely alone: even
    // re-inserting a node where it already is counts as a move.
    const wanted = previous ? previous.nextSibling : parent.firstChild;
    if (wanted !== element) parent.insertBefore(element, wanted);
    previous = element;
  }
  for (const gone of existing.values()) gone.remove();
}

/**
 * The socket, with reconnection and a polling fallback.
 *
 * `onEvent` is called with every message, heartbeats included -- filtering is
 * `affects`'s job, in one place. The polling fallback synthesises a RESYNC so
 * that callers have exactly one path for "something may have changed".
 */
export class LiveChannel {
  constructor({ url, onEvent, pollMs = POLL_MS, fallbackAfter = FALLBACK_AFTER }) {
    this.url = url;
    this.onEvent = onEvent;
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
    // A phone sleeps the moment it goes in a pocket and wakes on a different
    // network. Either event means: stop waiting out a backoff and try now.
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
    // Whatever happened while the screen was off is unknown, and the socket
    // may have died without either end noticing.
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
    // Some proxies drop an Upgrade without saying so, and then the socket
    // never connects however patiently we retry. Polling is worse in every
    // way except that it works, so it starts only once the socket has really
    // failed and stops the moment one connects.
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
