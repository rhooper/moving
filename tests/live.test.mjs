// The decisions the live-update client makes, away from the DOM and the socket:
// when to reconnect, which events matter to which view, and -- the one that
// actually bites a person -- when a refresh must be held back because someone
// is mid-edit or mid-tap.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  affects,
  backoffDelay,
  hasUnsavedEdits,
  holdRefresh,
  isDirty,
  onlySummaryChanged,
  partOf,
  reconcile,
} from "../web/live.js";

// --- reconnect backoff ------------------------------------------------------

test("the first retry is quick, so a blip is invisible", () => {
  // random() at 0.5 lands exactly in the middle of the jitter window.
  assert.equal(backoffDelay(0, { random: () => 0.5 }), 500);
});

test("each further attempt waits longer", () => {
  const half = { random: () => 0.5 };
  const delays = [0, 1, 2, 3, 4].map((n) => backoffDelay(n, half));
  assert.deepEqual(delays, [500, 1000, 2000, 4000, 8000]);
});

test("the wait is capped, so a phone that wakes up does not sit out a minute", () => {
  assert.equal(backoffDelay(30, { random: () => 0.5 }), 30000);
  assert.equal(backoffDelay(1000, { random: () => 0.5 }), 30000);
});

test("the wait is jittered, so every phone in the house does not reconnect at once", () => {
  assert.equal(backoffDelay(2, { random: () => 0 }), 1500); // 2000 * 0.75
  assert.equal(backoffDelay(2, { random: () => 1 }), 2500); // 2000 * 1.25
  for (let n = 0; n < 8; n += 1) {
    const delay = backoffDelay(n);
    assert.ok(delay > 0 && delay <= 30000 * 1.25, `delay ${delay} out of range`);
  }
});

// --- which events matter to which view --------------------------------------

const list = { name: "list", clientId: "me" };
const detail = { name: "box", code: "B-0007", clientId: "me" };

test("a heartbeat is not a change", () => {
  assert.equal(affects({ kind: "ping" }, list), false);
  assert.equal(affects({ kind: "ping" }, detail), false);
});

test("a resync marker always forces a refetch", () => {
  // It is what the server sends when it gave up on queueing a backlog, so the
  // client has no idea what it missed.
  assert.equal(affects({ kind: "resync" }, list), true);
  assert.equal(affects({ kind: "resync" }, detail), true);
});

test("the list cares about anything that changes a row", () => {
  for (const kind of ["box.created", "box.deleted", "box.status", "box.location", "box.updated"]) {
    assert.equal(affects({ kind, code: "B-0001" }, list), true, kind);
  }
});

test("the list cares about items too, because search matches on them", () => {
  assert.equal(affects({ kind: "items.changed", code: "B-0001" }, list), true);
});

test("the list ignores what only a box page shows", () => {
  assert.equal(affects({ kind: "label.printed", code: "B-0001" }, list), false);
});

test("the list cares about photos, because a row shows one", () => {
  // It did not before: a photo changed nothing a list row drew. Now the cover
  // thumbnail *is* part of the row, so a picture arriving, being deleted, or
  // being swapped for another one changes what the list shows -- and a phone
  // still looking at the list would otherwise keep drawing the old picture,
  // or a blank square, until something else happened to that box.
  assert.equal(affects({ kind: "photos.changed", code: "B-0001" }, list), true);
});

test("a box page only cares about its own box", () => {
  assert.equal(affects({ kind: "box.status", code: "B-0007" }, detail), true);
  assert.equal(affects({ kind: "photos.changed", code: "B-0007" }, detail), true);
  assert.equal(affects({ kind: "label.printed", code: "B-0007" }, detail), true);
  assert.equal(affects({ kind: "box.status", code: "B-0001" }, detail), false);
});

test("a device ignores the echo of its own change", () => {
  // It has already redrawn from the response it got. Redrawing again on the
  // echo is what throws away half-typed text.
  assert.equal(affects({ kind: "box.status", code: "B-0007", origin: "me" }, detail), false);
  assert.equal(affects({ kind: "box.status", code: "B-0007", origin: "other" }, detail), true);
  assert.equal(affects({ kind: "box.created", code: "B-0001", origin: "me" }, list), false);
});

test("nonsense on the wire changes nothing", () => {
  assert.equal(affects(null, list), false);
  assert.equal(affects({}, list), false);
  assert.equal(affects({ kind: "box.status", code: "B-0007" }, null), false);
  assert.equal(affects({ kind: "box.status", code: "B-0007" }, { name: "scan" }), false);
});

// --- not clobbering someone mid-edit ----------------------------------------

test("a form nobody has touched is safe to redraw", () => {
  assert.equal(hasUnsavedEdits([]), false);
  assert.equal(hasUnsavedEdits([{ value: "kettle", initial: "kettle" }]), false);
});

test("a field that differs from what was drawn is an unsaved edit", () => {
  assert.equal(hasUnsavedEdits([{ value: "kettle, mugs", initial: "kettle" }]), true);
  assert.equal(hasUnsavedEdits([{ value: "", initial: "kettle" }]), true);
});

test("an empty field someone is typing into counts too", () => {
  // The value still matches what was drawn, but redrawing drops focus and
  // shuts the on-screen keyboard mid-word.
  assert.equal(hasUnsavedEdits([{ value: "", initial: "", focused: true }]), true);
});

test("a dropdown moved but not saved is an unsaved edit", () => {
  assert.equal(hasUnsavedEdits([{ value: "3", initial: "1" }]), true);
});

// --- not moving the list under a thumb --------------------------------------

test("nothing happening means refresh away", () => {
  assert.equal(holdRefresh({}, 10000), false);
});

test("a finger on the screen holds the refresh", () => {
  // Replacing a row between touchstart and click sends the tap to whichever
  // box slid into that spot.
  assert.equal(holdRefresh({ pointerDown: true }, 10000), true);
});

test("the hold outlasts the tap by a settling window", () => {
  assert.equal(holdRefresh({ lastTouch: 9800 }, 10000), true);
  assert.equal(holdRefresh({ lastTouch: 9000 }, 10000), false);
});

test("an unsaved edit holds the refresh", () => {
  assert.equal(holdRefresh({ editing: true }, 10000), true);
});

test("nothing but an edit or a gesture holds the refresh", () => {
  // There used to be a third reason -- an unaccepted AI draft on screen. The
  // review panel is gone (analysis is applied server-side), and a leftover
  // flag nobody sets must not be able to hold the screen.
  assert.equal(holdRefresh({ drafting: true }, 10000), false);
});

// --- updating one part of an open box, even while the rest is held ----------

test("items, photos and the box row each map to one part of the box page", () => {
  assert.equal(partOf({ kind: "items.changed", code: "B-0007" }), "items");
  assert.equal(partOf({ kind: "photos.changed", code: "B-0007" }), "photos");
  assert.equal(partOf({ kind: "box.updated", code: "B-0007" }), "summary");
});

test("anything else has no part of its own and means a whole refresh", () => {
  // A resync most of all: nobody knows what was missed.
  for (const kind of ["resync", "box.status", "box.location", "box.deleted", "label.printed"]) {
    assert.equal(partOf({ kind }), null, kind);
  }
  assert.equal(partOf(null), null);
  assert.equal(partOf({}), null);
});

const drawn = {
  code: "B-0007", content_summary: "kettle", summary_source: "auto",
  fragile: 0, status: "open", updated_at: "2026-09-18 10:00:00",
};

test("a rewritten summary alone can be applied without redrawing the page", () => {
  const fresh = { ...drawn, content_summary: "kettle, 3 mugs", updated_at: "2026-09-18 10:00:09" };
  assert.equal(onlySummaryChanged(drawn, fresh), true);
  // The same goes for who wrote it.
  assert.equal(onlySummaryChanged(drawn, { ...fresh, summary_source: "manual" }), true);
});

test("nothing changed at all is also fine to apply in place", () => {
  assert.equal(onlySummaryChanged(drawn, { ...drawn }), true);
});

test("a change to anything else the page draws needs the whole page", () => {
  assert.equal(onlySummaryChanged(drawn, { ...drawn, fragile: 1 }), false);
  assert.equal(onlySummaryChanged(drawn, { ...drawn, status: "packed" }), false);
  // A key appearing or vanishing counts: the server grew a field this page
  // may be drawing.
  assert.equal(onlySummaryChanged(drawn, { ...drawn, deleted_at: "2026-09-18" }), false);
  const { fragile: _gone, ...without } = drawn;
  assert.equal(onlySummaryChanged(drawn, without), false);
});

test("a box that could not be compared is treated as changed", () => {
  assert.equal(onlySummaryChanged(null, drawn), false);
  assert.equal(onlySummaryChanged(drawn, null), false);
});

// --- keeping a row the same element across a refresh ------------------------
//
// The whole point of reconcile is node *identity*: a row that survives a
// refresh must be the same object, or a tap that began on it is delivered to
// whatever replaced it. Node has no DOM, so these run against a stand-in for
// the four things reconcile uses -- children, firstChild, nextSibling,
// insertBefore -- which is precisely the surface being relied on.

class FakeNode {
  constructor(key) {
    this.dataset = { key };
    this.parent = null;
    this.prev = null;
    this.next = null;
    this.seen = null;
  }
  get nextSibling() {
    return this.next;
  }
  remove() {
    this.parent.detach(this);
  }
}

class FakeParent {
  constructor() {
    this.first = null;
  }
  get firstChild() {
    return this.first;
  }
  get children() {
    const out = [];
    for (let node = this.first; node; node = node.next) out.push(node);
    return out;
  }
  detach(node) {
    if (node.prev) node.prev.next = node.next;
    else this.first = node.next;
    if (node.next) node.next.prev = node.prev;
    node.prev = null;
    node.next = null;
    node.parent = null;
  }
  insertBefore(node, ref) {
    if (node.parent === this) this.detach(node);
    let last = this.first;
    while (last && last.next) last = last.next;
    node.parent = this;
    node.prev = ref ? ref.prev : last;
    node.next = ref || null;
    if (node.prev) node.prev.next = node;
    else this.first = node;
    if (node.next) node.next.prev = node;
    return node;
  }
  keys() {
    return this.children.map((node) => node.dataset.key);
  }
}

const rows = {
  key: (box) => box.code,
  create: (box) => new FakeNode(box.code),
  update: (node, box) => {
    node.seen = box.status ?? null;
  },
};

const patch = (parent, codes) =>
  reconcile(parent, codes.map((code) => ({ code })), rows);

function listOf(codes) {
  const parent = new FakeParent();
  patch(parent, codes);
  return parent;
}

test("an empty list fills in order", () => {
  assert.deepEqual(listOf(["A", "B", "C"]).keys(), ["A", "B", "C"]);
});

test("a patch that changes nothing recreates nothing", () => {
  const parent = listOf(["A", "B", "C"]);
  const before = parent.children;

  patch(parent, ["A", "B", "C"]);

  assert.deepEqual(parent.keys(), ["A", "B", "C"]);
  assert.deepEqual(parent.children, before, "rows were replaced by a no-op patch");
});

test("a new box at the top leaves the rows below it alone", () => {
  // Boxes come back newest first, so this is what every create looks like --
  // and the row your thumb is on is one of the ones that must not move.
  const parent = listOf(["A", "B", "C"]);
  const [, b, c] = parent.children;

  patch(parent, ["D", "A", "B", "C"]);

  assert.deepEqual(parent.keys(), ["D", "A", "B", "C"]);
  assert.equal(parent.children[2], b);
  assert.equal(parent.children[3], c);
});

test("a box deleted elsewhere takes only its own row", () => {
  const parent = listOf(["A", "B", "C"]);
  const c = parent.children[2];

  patch(parent, ["A", "C"]);

  assert.deepEqual(parent.keys(), ["A", "C"]);
  assert.equal(parent.children[1], c, "C was recreated when B went");
});

test("a reorder moves rows without rebuilding them", () => {
  const parent = listOf(["A", "B", "C"]);
  const [a, b, c] = parent.children;

  patch(parent, ["C", "A", "B"]);

  assert.deepEqual(parent.keys(), ["C", "A", "B"]);
  assert.deepEqual(parent.children, [c, a, b]);
});

test("a wholly different result set replaces everything", () => {
  const parent = listOf(["A", "B"]);

  patch(parent, ["X", "Y"]);

  assert.deepEqual(parent.keys(), ["X", "Y"]);
});

test("a surviving row has its contents brought up to date", () => {
  const parent = listOf(["A"]);
  const a = parent.children[0];

  reconcile(parent, [{ code: "A", status: "packed" }], rows);

  assert.equal(parent.children[0], a);
  assert.equal(a.seen, "packed");
});

test("emptying the list removes every row", () => {
  const parent = listOf(["A", "B"]);

  patch(parent, []);

  assert.deepEqual(parent.keys(), []);
});

// --- cancelling an edit -------------------------------------------------------
//
// Cancel appears only once there is something to cancel, so its rule is
// stricter than hasUnsavedEdits: focus alone is not a change.

test("a form as it was drawn has nothing to cancel", () => {
  assert.equal(isDirty([]), false);
  assert.equal(isDirty([{ value: "kettle", initial: "kettle" }]), false);
});

test("tapping into a field is not an edit", () => {
  assert.equal(isDirty([{ value: "kettle", initial: "kettle", focused: true }]), false);
});

test("any one changed field makes the form cancellable", () => {
  assert.equal(
    isDirty([
      { value: "box", initial: "box" },
      { value: "3", initial: "" },
    ]),
    true,
  );
});

test("typing a change and typing it back leaves nothing to cancel", () => {
  assert.equal(isDirty([{ value: "kettle", initial: "kettle" }]), false);
});

test("a field that was drawn empty and is still empty is clean", () => {
  // dataset.initial is absent for a field drawn with no value.
  assert.equal(isDirty([{ value: "", initial: undefined }]), false);
});

test("a row whose create forgot to key it is still found next time", () => {
  // This happened: the photo strip's figures were made without data-key, so
  // every update missed them all, drew a second copy of the strip beneath the
  // first, and removed only one stale figure (they all shared the key
  // `undefined`). reconcile knows the key, so it sets it rather than trusting
  // every create to remember.
  const forgetful = { ...rows, create: () => new FakeNode(undefined) };
  const parent = new FakeParent();
  const draw = (codes) => reconcile(parent, codes.map((code) => ({ code })), forgetful);

  draw(["A", "B"]);
  const before = parent.children;
  draw(["A", "B"]);

  assert.deepEqual(parent.keys(), ["A", "B"]);
  assert.deepEqual(parent.children, before, "the rows were duplicated or replaced");
});
