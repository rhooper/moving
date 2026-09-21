// Autosave with undo. No DOM and time injected, so it is tested without a
// browser: app.js owns the fields, this owns when they save, what is unsaved,
// and Undo. Values are compared as strings, as form fields hold them.

// When a field should be saved:
//
//   "change"  the moment it changes: a picker or a tick box.
//   "pause"   after a pause in typing, and on leaving the field.
//   "commit"  only on leaving the field or pressing Return, for a field whose
//             every save is recorded: the location's history would otherwise
//             log "garage st" as a place it had been.
export function policyFor(field) {
  const asked = field.dataset?.autosave;
  if (asked) return asked;
  // A radio is one button of a pushbutton row: pressing it is a whole choice.
  if (field.tagName === "SELECT" || field.type === "checkbox" || field.type === "radio") return "change";
  return "pause";
}

// The status line under a form. `fields` are `{ state, policy, refused? }` for
// each field behind it; `last` is "saved", "undone" or null. Typing reads as
// "Saving…" from the first keystroke: the pause is part of the save.
export function lineFor(fields, last = null) {
  const states = fields || [];
  const failed = states.filter((f) => f.state === "failed");
  // `refused` is the server's reason for a failure that will not be retried:
  // shown first, as the one state that needs a person.
  const refused = failed.find((f) => f.refused);
  if (refused) return { text: `Not saved — ${refused.refused}`, warn: true };
  if (failed.length) return { text: "Not saved yet — will retry", warn: true };
  if (states.some((f) => f.state === "saving")) return { text: "Saving…", warn: false };
  const waiting = states.filter((f) => f.state === "unsaved");
  if (waiting.some((f) => f.policy !== "commit")) return { text: "Saving…", warn: false };
  if (waiting.length) {
    // Return saves it too, but this must fit beside Undo at 320 px.
    return { text: "Saves when you leave the field", warn: false };
  }
  const said = { saved: "Saved", undone: "Undone" }[last] || "";
  return { text: said, warn: false };
}

// Delay before a retry, given the failures in a row so far (0-based).
const RETRY_BASE_MS = 2000;
const RETRY_CAP_MS = 30000;
export const retryAfter = (attempt) => Math.min(RETRY_CAP_MS, RETRY_BASE_MS * 2 ** attempt);

export class Autosaver {
  constructor({
    save,                       // async (key, value) => void; throws on failure
    delay = 1200,               // ms of quiet before a "pause" field is saved
    depth = 20,                 // how many saves can be undone
    onState = () => {},         // (key, "saving" | "saved" | "failed", error?) => void
    retryDelay = retryAfter,    // (failures so far, error) => ms until the next
                                //   try, or null: not worth retrying
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

  // Tracks a field at the server's value. Also how a live update lands: a new
  // baseline, not an edit to save back.
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

  edit(key, value, policy = "pause") {
    const field = this.fields.get(key);
    if (!field) return;
    field.value = String(value ?? "");
    // A waiting retry goes too: on a "commit" field it would save mid-word. The
    // field stays "failed" until a save lands.
    this._stopTimers(field);

    if (policy === "change") {
      this._flush(key);
    } else if (policy === "pause") {
      field.timer = this.setTimer(() => { field.timer = null; this._flush(key); }, this.delay);
    }
    // "commit": nothing until commit() is called.
  }

  // The field was left or Return pressed; also the retry after a failure.
  commit(key) {
    const field = this.fields.get(key);
    if (!field) return;
    this._stopTimers(field);
    this._flush(key);
  }

  // The page is being left, hidden or redrawn: a pause not yet run out must
  // not cost the edit.
  commitAll() {
    for (const key of this.fields.keys()) this.commit(key);
  }

  // Only what failed: a "commit" field has not been left because the network
  // came back.
  retryFailed() {
    for (const [key, field] of this.fields) if (field.failed) this.commit(key);
  }

  // Drops what the server has not got and returns what it has. Taking back a
  // failed edit needs no request; undo() would reach past it.
  revert(key) {
    const field = this.fields.get(key);
    if (!field) return undefined;
    this._stopTimers(field);
    field.value = field.saved;
    field.failed = false;
    field.failures = 0;
    return field.saved;
  }

  // Resolves when no save is in flight, landed or failed. For what must come
  // after them: a redraw fetched too early shows old text as current, and an
  // undo racing a save can reach the server first.
  async idle() {
    for (;;) {
      const out = [...this.fields.values()].map((field) => field.flight).filter(Boolean);
      if (!out.length) return;
      await Promise.all(out);
    }
  }

  // "clean", "unsaved", "saving" or "failed" (kept and retried; only a save
  // that lands clears it), or null if not tracked.
  state(key) {
    const field = this.fields.get(key);
    if (!field) return null;
    if (field.saving) return "saving";
    if (field.failed) return "failed";
    return field.value === field.saved ? "clean" : "unsaved";
  }

  // The saver outlives redraws, so this is how unsaved text gets back into a
  // redrawn field.
  value(key) {
    return this.fields.get(key)?.value;
  }

  // Whether a field (with no key, any field) holds what the server has not got.
  unsaved(key) {
    if (key === undefined) return [...this.fields.keys()].some((k) => this.unsaved(k));
    const field = this.fields.get(key);
    return Boolean(field) && (field.value !== field.saved || field.saving);
  }

  // The key the next undo() would restore, or null.
  canUndo() {
    return this.undoable.length ? this.undoable.at(-1).key : null;
  }

  // Takes back the most recent save, resolving to { key, value } or null. An
  // undo is not itself undoable.
  async undo() {
    const step = this.undoable.at(-1);
    if (!step) return null;
    const field = this.fields.get(step.key);
    if (field?.timer) { this.clearTimer(field.timer); field.timer = null; }

    await this.save(step.key, step.from);   // throws -> the step stays put
    // Only now: had the undo failed, a waiting retry of a newer edit is wanted.
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

  _flush(key) {
    const field = this.fields.get(key);
    if (!field || field.saving) return;   // the in-flight save re-checks when it lands
    if (field.value === field.saved) return;
    // For idle() to wait on. Never rejects: a failure is a state.
    const flight = this._send(key, field).finally(() => {
      if (field.flight === flight) field.flight = null;
    });
    field.flight = flight;
  }

  async _send(key, field) {
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
      // The edit stays, and a timer retries it so "will retry" holds true.
      if (field.retry) { this.clearTimer(field.retry); field.retry = null; }
      // null: the server refused, and asking again gets the same answer.
      const wait = this.retryDelay(field.failures, error);
      if (wait !== null && wait !== undefined) {
        field.retry = this.setTimer(() => { field.retry = null; this._flush(key); }, wait);
      }
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

    // Typed on while in flight: send the newer text. idle() finds this next
    // flight on its next look.
    if (field.value !== field.saved && !field.timer) this._flush(key);
  }
}
