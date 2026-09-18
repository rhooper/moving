// Autosave with undo, in place of Save and Cancel buttons.
//
// Pure logic: no DOM, time injected, and "save" is whatever async function the
// page hands in -- so it is tested in tests/autosave.test.mjs without a
// browser. app.js owns the fields; this owns *when* they are saved, what is
// still unsaved, and how to take a save back.
//
// Values are compared as strings, which is what form fields hold.

// When a field should be saved:
//
//   "change"  the moment it changes -- a picker or a tick box; there is no
//             half-made choice to wait out.
//   "pause"   after a pause in typing, and on leaving the field.
//   "commit"  only on leaving the field or pressing Return. For a field whose
//             every save is recorded somewhere: the current location writes to
//             the box's history, and saving mid-word would log "garage st" as
//             a place it had been.
export function policyFor(field) {
  const asked = field.dataset?.autosave;
  if (asked) return asked;
  if (field.tagName === "SELECT" || field.type === "checkbox") return "change";
  return "pause";
}

/**
 * What the status line under a form says.
 *
 * `fields` are `{ state, policy }` for each field behind the line -- `state`
 * as `Autosaver.state()` reports it -- and `last` is what last happened in
 * this form: "saved", "undone", or nothing. A function of those and nothing
 * else, so the line can be drawn again from scratch after the page is.
 *
 * Typing reads as "Saving…" from the first keystroke: the pause is part of
 * the save, and a line that said "Saved" over text the server has not got
 * would be a small lie told every few seconds.
 */
export function lineFor(fields, last = null) {
  const states = fields || [];
  if (states.some((f) => f.state === "failed")) {
    return { text: "Not saved yet — will retry", warn: true };
  }
  if (states.some((f) => f.state === "saving")) return { text: "Saving…", warn: false };
  const waiting = states.filter((f) => f.state === "unsaved");
  if (waiting.some((f) => f.policy !== "commit")) return { text: "Saving…", warn: false };
  if (waiting.length) {
    return { text: "Saves when you leave the field or press Return", warn: false };
  }
  const said = { saved: "Saved", undone: "Undone" }[last] || "";
  return { text: said, warn: false };
}

// How long to wait before retrying a save that failed, given how many have
// failed in a row before it (0-based). Doubling and capped: quick enough that a
// blip is over before anybody notices, slow enough that a phone out of range
// for an hour is not hammering a server it cannot reach.
const RETRY_BASE_MS = 2000;
const RETRY_CAP_MS = 30000;
const retryAfter = (attempt) => Math.min(RETRY_CAP_MS, RETRY_BASE_MS * 2 ** attempt);

export class Autosaver {
  constructor({
    save,                       // async (key, value) => void; throws on failure
    delay = 1200,               // ms of quiet before a "pause" field is saved
    depth = 20,                 // how many saves can be undone
    onState = () => {},         // (key, "saving" | "saved" | "failed", error?) => void
    retryDelay = retryAfter,    // (failures so far) => ms until the next try
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (id) => clearTimeout(id),
  }) {
    this.save = save;
    this.delay = delay;
    this.depth = depth;
    this.onState = onState;
    this.retryDelay = retryDelay;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    // key -> { saved, value, timer, retry, failures, saving, failed }
    this.fields = new Map();
    this.undoable = [];         // [{ key, from }], oldest first
  }

  // Start (or restart) tracking a field at the value the server holds. Also
  // how a live update lands: a pristine field rewritten by the server has a
  // new baseline, not an edit to save back.
  track(key, value) {
    const field = this.fields.get(key);
    if (field) this._stopTimers(field);
    this.fields.set(key, {
      saved: String(value ?? ""),
      value: String(value ?? ""),
      timer: null,
      retry: null,
      failures: 0,
      saving: false,
      failed: false,
    });
  }

  _stopTimers(field) {
    if (field.timer) { this.clearTimer(field.timer); field.timer = null; }
    if (field.retry) { this.clearTimer(field.retry); field.retry = null; }
  }

  // The field's value changed.
  edit(key, value, policy = "pause") {
    const field = this.fields.get(key);
    if (!field) return;
    field.value = String(value ?? "");
    // A waiting retry goes too. Every policy has its own next moment to save,
    // and for a field that saves only when it is left, a retry coming round
    // mid-word would do exactly what that policy exists to prevent. The field
    // stays "failed" until a save lands, so the page keeps saying so.
    this._stopTimers(field);

    if (policy === "change") {
      this._flush(key);
    } else if (policy === "pause") {
      field.timer = this.setTimer(() => { field.timer = null; this._flush(key); }, this.delay);
    }
    // "commit": nothing until commit() is called.
  }

  // The field was left, or Return was pressed in it: save now if there is
  // anything to save. Also the retry after a failure.
  commit(key) {
    const field = this.fields.get(key);
    if (!field) return;
    this._stopTimers(field);
    this._flush(key);
  }

  // Everything that is waiting, now: the page is being left, hidden or
  // redrawn, and a pause that has not run out yet must not cost the edit.
  commitAll() {
    for (const key of this.fields.keys()) this.commit(key);
  }

  // The network came back. Only what failed: a half-typed field that saves
  // on being left has not been left just because the wifi reconnected.
  retryFailed() {
    for (const [key, field] of this.fields) if (field.failed) this.commit(key);
  }

  // One word for where a field stands, for a page drawn after the events
  // that would have told it: "clean", "unsaved" (edited, waiting for its
  // moment), "saving", or "failed" (kept, and being retried). Failed outlives
  // further typing -- only a save that lands clears it. null if not tracked.
  state(key) {
    const field = this.fields.get(key);
    if (!field) return null;
    if (field.saving) return "saving";
    if (field.failed) return "failed";
    return field.value === field.saved ? "clean" : "unsaved";
  }

  // What the field was last known to hold. The saver outlives the fields (the
  // page is redrawn under it), so this is how unsaved text gets back into a
  // field that was redrawn from a server that never received it.
  value(key) {
    return this.fields.get(key)?.value;
  }

  // Whether a field (or, with no key, any field) holds something the server
  // does not have yet. The page uses this to hold back a redraw.
  unsaved(key) {
    if (key === undefined) return [...this.fields.keys()].some((k) => this.unsaved(k));
    const field = this.fields.get(key);
    return Boolean(field) && (field.value !== field.saved || field.saving);
  }

  // The key the next undo() would restore, or null.
  canUndo() {
    return this.undoable.length ? this.undoable.at(-1).key : null;
  }

  // Take back the most recent save. Resolves to { key, value } so the page
  // can put the value back in the field, or null if there is nothing to undo.
  // Not itself undoable: a redo stack is one more thing to get wrong for a
  // case -- "undo my undo" -- that retyping covers.
  async undo() {
    const step = this.undoable.at(-1);
    if (!step) return null;
    const field = this.fields.get(step.key);
    if (field?.timer) { this.clearTimer(field.timer); field.timer = null; }

    await this.save(step.key, step.from);   // throws -> the step stays put
    // Only now: had the undo failed, a retry still waiting for a newer edit
    // of this field is still wanted.
    if (field) this._stopTimers(field);
    this.undoable.pop();
    if (field) {
      field.saved = step.from;
      field.value = step.from;
      field.failed = false;
      field.failures = 0;
    }
    return { key: step.key, value: step.from };
  }

  async _flush(key) {
    const field = this.fields.get(key);
    if (!field || field.saving) return;   // the in-flight save re-checks when it lands
    if (field.value === field.saved) return;

    const sending = field.value;
    const previous = field.saved;
    field.saving = true;
    this.onState(key, "saving");
    try {
      await this.save(key, sending);
    } catch (error) {
      field.saving = false;
      // track() may have replaced this field while the save was out; a new
      // baseline from the server is not something to retry over.
      if (this.fields.get(key) !== field) return;
      field.failed = true;
      // The edit stays. commit() or the next edit retries it, and so does
      // this, so that "will retry" is true with nobody touching anything.
      if (field.retry) this.clearTimer(field.retry);
      field.retry = this.setTimer(() => { field.retry = null; this._flush(key); },
                                  this.retryDelay(field.failures));
      field.failures += 1;
      this.onState(key, "failed", error);
      return;
    }
    field.saving = false;
    field.failed = false;
    field.failures = 0;
    if (field.retry) { this.clearTimer(field.retry); field.retry = null; }
    field.saved = sending;
    this.undoable.push({ key, from: previous });
    if (this.undoable.length > this.depth) this.undoable.shift();
    this.onState(key, "saved");

    // Typed on while that was in flight: what is in the field now is newer.
    if (field.value !== field.saved && !field.timer) this._flush(key);
  }
}
