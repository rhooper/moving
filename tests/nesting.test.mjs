// Things inside things, as the page sees them: the way out of a nested record,
// the words for what is inside, and whether one record may go inside another
// -- decided here, before the server is asked, so the page never offers a move
// the server would refuse.
import assert from "node:assert/strict";
import { test } from "node:test";

import { blockedDelete, describe, inheritedRoom, mayHold, notYetFragile, trail } from "../web/nesting.js";

// --- naming a container ----------------------------------------------------------

test("a container is named by its summary and its kind", () => {
  assert.equal(describe({ code: "B-0012", kind: "crate", content_summary: "kitchen" }), "kitchen crate");
  assert.equal(describe({ code: "B-0012", kind: "box", content_summary: "" }), "box");
  assert.equal(describe({ code: "B-0012", kind: "box", content_summary: null }), "box");
});

test("a summary that already ends with the kind is not doubled", () => {
  assert.equal(describe({ kind: "crate", content_summary: "kitchen crate" }), "kitchen crate");
  assert.equal(describe({ kind: "box", content_summary: "The big BOX" }), "The big BOX");
});

// --- the way out ---------------------------------------------------------------------

test("the breadcrumb is the path, outermost first, each step named", () => {
  const steps = trail([
    { code: "B-0010", kind: "crate", content_summary: "kitchen" },
    { code: "B-0012", kind: "box", content_summary: "" },
  ]);

  assert.deepEqual(steps, [
    { code: "B-0010", hint: "kitchen crate" },
    { code: "B-0012", hint: "box" },
  ]);
});

test("a top-level record has no breadcrumb", () => {
  assert.deepEqual(trail([]), []);
  assert.deepEqual(trail(null), []);
});

// --- may this go inside that -------------------------------------------------------------
//
// The server refuses all of these too (tests/test_nesting.py). Deciding them
// here as well means the page says why at once, and never offers the move.

const kinds = [
  { kind: "crate", contents: true }, { kind: "box", contents: true },
  { kind: "item", contents: false }, { kind: "furniture", contents: false },
];
const me = { code: "B-0012", kind: "box" };
const children = [{ code: "B-0013", kind: "bag" }, { code: "B-0014", kind: "item" }];

test("a container that is nothing to do with this record may hold it", () => {
  const crate = { code: "B-0010", kind: "crate", deleted_at: null };
  assert.deepEqual(mayHold(crate, me, children, kinds), { ok: true, why: "" });
});

test("not itself", () => {
  const said = mayHold({ code: "B-0012", kind: "box" }, me, children, kinds);
  assert.equal(said.ok, false);
  assert.match(said.why, /itself/);
});

test("not something that is inside it", () => {
  const said = mayHold({ code: "B-0013", kind: "bag" }, me, children, kinds);
  assert.equal(said.ok, false);
  assert.match(said.why, /B-0013 is inside/);
});

test("not a single thing: a lamp has no inside", () => {
  const lamp = { code: "B-0020", kind: "item" };
  const said = mayHold(lamp, me, children, kinds);
  assert.equal(said.ok, false);
  assert.match(said.why, /not a container/);
});

test("not something in the bin", () => {
  const binned = { code: "B-0021", kind: "crate", deleted_at: "2026-09-20 09:00:00" };
  const said = mayHold(binned, me, children, kinds);
  assert.equal(said.ok, false);
  assert.match(said.why, /bin/);
});

test("nothing found is not a container either", () => {
  assert.equal(mayHold(null, me, children, kinds).ok, false);
});

// --- a container holding things cannot be deleted ------------------------------------------

test("delete is explained, not offered, while things are inside", () => {
  assert.equal(blockedDelete([{ code: "B-0013" }, { code: "B-0014" }, { code: "B-0015" }]),
               "Move the 3 things inside it out first.");
  assert.equal(blockedDelete([{ code: "B-0013" }]), "Move the thing inside it out first.");
  assert.equal(blockedDelete([]), "");
});

// --- a nested record goes where its container goes ---------------------------------------
//
// "subitems should hide the destination input": a thing inside a crate goes
// wherever the crate goes, so it has no room of its own to choose. The room is
// the nearest container's -- path is outermost first, so the search runs from
// the end -- and a container with no room of its own defers to the one it is in.

test("the room is the nearest container's", () => {
  const path = [
    { code: "B-0001", kind: "crate", destination_room_id: 3 },
    { code: "B-0002", kind: "box", destination_room_id: 5 },
  ];
  assert.deepEqual(inheritedRoom(path), { code: "B-0002", room: 5 });
});

test("a container with no room of its own passes the question outwards", () => {
  const path = [
    { code: "B-0001", kind: "crate", destination_room_id: 3 },
    { code: "B-0002", kind: "box", destination_room_id: null },
  ];
  assert.deepEqual(inheritedRoom(path), { code: "B-0001", room: 3 });
});

test("no container has a room: the nearest one is named, with no room", () => {
  // The page still says whose room it will be, so "no room chosen" is about
  // the container, not this record.
  const path = [
    { code: "B-0001", kind: "crate", destination_room_id: null },
    { code: "B-0002", kind: "box", destination_room_id: null },
  ];
  assert.deepEqual(inheritedRoom(path), { code: "B-0002", room: null });
});

test("a top-level record inherits nothing", () => {
  assert.equal(inheritedRoom([]), null);
  assert.equal(inheritedRoom(null), null);
});

// --- fragile climbs --------------------------------------------------------------------------
//
// "fragile should percolate up to the parent and set that (prompt to set if
// it's not set) but don't undo on clear." The page asks about the containers
// that are not yet fragile; which those are is decided here.

test("the containers not yet marked fragile, outermost first", () => {
  const path = [
    { code: "B-0001", kind: "crate", fragile: 0 },
    { code: "B-0002", kind: "box", fragile: 1 },
    { code: "B-0003", kind: "bag", fragile: null },
  ];
  assert.deepEqual(notYetFragile(path).map((s) => s.code), ["B-0001", "B-0003"]);
});

test("every container already fragile means nothing to ask", () => {
  assert.deepEqual(notYetFragile([{ code: "B-0001", fragile: 1 }, { code: "B-0002", fragile: true }]), []);
  assert.deepEqual(notYetFragile([]), []);
  assert.deepEqual(notYetFragile(null), []);
});
