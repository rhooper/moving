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

export class Autosaver {
  constructor({
    save,                       // async (key, value) => void; throws on failure
    delay = 1200,               // ms of quiet before a "pause" field is saved
    depth = 20,                 // how many saves can be undone
    onState = () => {},         // (key, "saving" | "saved" | "failed") => void
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (id) => clearTimeout(id),
  }) {
    this.save = save;
    this.delay = delay;
    this.depth = depth;
    this.onState = onState;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.fields = new Map();    // key -> { saved, value, timer, saving, failed }
    this.undoable = [];         // [{ key, from }], oldest first
  }

  // Start (or restart) tracking a field at the value the server holds. Also
  // how a live update lands: a pristine field rewritten by the server has a
  // new baseline, not an edit to save back.
  track(key, value) {
    const field = this.fields.get(key);
    if (field?.timer) this.clearTimer(field.timer);
    this.fields.set(key, {
      saved: String(value ?? ""),
      value: String(value ?? ""),
      timer: null,
      saving: false,
      failed: false,
    });
  }

  // The field's value changed.
  edit(key, value, policy = "pause") {
    const field = this.fields.get(key);
    if (!field) return;
    field.value = String(value ?? "");
    if (field.timer) { this.clearTimer(field.timer); field.timer = null; }

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
    if (field.timer) { this.clearTimer(field.timer); field.timer = null; }
    this._flush(key);
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
    this.undoable.pop();
    if (field) {
      field.saved = step.from;
      field.value = step.from;
      field.failed = false;
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
    } catch {
      field.saving = false;
      field.failed = true;
      this.onState(key, "failed");   // the edit stays; commit() or the next edit retries
      return;
    }
    field.saving = false;
    field.failed = false;
    field.saved = sending;
    this.undoable.push({ key, from: previous });
    if (this.undoable.length > this.depth) this.undoable.shift();
    this.onState(key, "saved");

    // Typed on while that was in flight: what is in the field now is newer.
    if (field.value !== field.saved && !field.timer) this._flush(key);
  }
}
