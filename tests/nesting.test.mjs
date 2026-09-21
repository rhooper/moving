// Things inside things, as the page sees them: the way out of a nested record,
// the words for what is inside, and whether one record may go inside another
// -- decided here, before the server is asked, so the page never offers a move
// the server would refuse.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addedInside, addInsideRequest, blockedDelete, cameraTrouble, describe, editorSections, frameSize,
  groupMatches, inheritedRoom, kindsToAddInside, mayHold, notYetFragile, trail,
} from "../web/nesting.js";

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
// A thing inside a crate goes wherever the crate goes, so it has no room of its
// own to choose. The room is the nearest container's -- path is outermost
// first, so the search runs from the end -- and a container with no room of
// its own defers to the one it is in.

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
// The page offers to mark fragile the containers that are not yet; clearing
// never climbs. Which containers to ask about is decided here.

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

// --- adding something inside, from the container's page ---------------------------------
//
// The add-inside dialog asks for a kind, a photo and an optional source. What
// it offers and what it sends are decided here; the dialog only asks.

const everyKind = [
  { kind: "box", label: "Box", contents: true }, { kind: "bag", label: "Bag", contents: true },
  { kind: "item", label: "Loose item", contents: false }, { kind: "furniture", label: "Furniture", contents: false },
];

test("a container can hold a container or a single thing: every kind is offered, in order", () => {
  assert.deepEqual(kindsToAddInside(everyKind).map((k) => k.kind), ["box", "bag", "item", "furniture"]);
  assert.deepEqual(kindsToAddInside([]), []);
});

test("the dialog sends the kind, where it is, and where it came from -- nothing else", () => {
  assert.deepEqual(addInsideRequest({ kind: "bag", parentCode: "B-0001", sourceRoom: "3" }),
                   { kind: "bag", parent_code: "B-0001", source_room_id: 3 });
});

test("no source room is null, not an empty string", () => {
  assert.deepEqual(addInsideRequest({ kind: "bag", parentCode: "B-0001", sourceRoom: "" }),
                   { kind: "bag", parent_code: "B-0001", source_room_id: null });
  assert.deepEqual(addInsideRequest({ kind: "bag", parentCode: "B-0001" }),
                   { kind: "bag", parent_code: "B-0001", source_room_id: null });
});

test("what was added is named, and a photo that did not upload is said, not dropped", () => {
  const made = { code: "B-0009", kind: "bag" };
  assert.deepEqual(addedInside(made, null), { text: "Added B-0009 (bag).", warn: false });
  assert.deepEqual(addedInside(made, new Error("the server is unreachable")), {
    text: "Added B-0009 (bag), but its photo did not upload: the server is unreachable. Add one from its page.",
    warn: true,
  });
});

// --- what the sub-item editor shows, and what it folds away -----------------------
//
// The rule is about *content*, not which field it is: a section with something
// in it is open, an empty one is folded. Nothing with content is ever hidden,
// or somebody edits a record without seeing what is already on it.

const bagShape = { kind: "bag", label: "Bag", contents: true, sizes: ["small", "large"] };
const lampShape = { kind: "item", label: "Loose item", contents: false, sizes: [] };
const bare = { code: "B-0009", kind: "bag", content_summary: "", size: null, source_room_id: null,
               source_location: null, fragile: 0, heavy: 0, open_first: 0 };
const sectionsOf = (...args) => editorSections(...args).map((s) => s.key);
const openOf = (...args) => editorSections(...args).filter((s) => s.open).map((s) => s.key);

test("an empty record folds everything away but the one thing it always has", () => {
  // A bag just dropped into a crate: nothing typed, nothing chosen.
  assert.deepEqual(sectionsOf(bare, [], bagShape), ["summary", "kind", "size", "source", "handling", "items"]);
  assert.deepEqual(openOf(bare, [], bagShape), ["kind"]);
});

test("every section that has something in it starts open", () => {
  const full = { ...bare, content_summary: "cutlery", size: "small", source_room_id: 3, fragile: 1 };
  assert.deepEqual(openOf(full, [{ id: 1, name: "forks" }], bagShape),
                   ["summary", "kind", "size", "source", "handling", "items"]);
});

test("each section is opened by its own content and nothing else", () => {
  assert.deepEqual(openOf({ ...bare, content_summary: "cutlery" }, [], bagShape), ["summary", "kind"]);
  assert.deepEqual(openOf({ ...bare, size: "large" }, [], bagShape), ["kind", "size"]);
  assert.deepEqual(openOf({ ...bare, source_room_id: 3 }, [], bagShape), ["kind", "source"]);
  // Where in that room is part of where it came from.
  assert.deepEqual(openOf({ ...bare, source_location: "shelf 3" }, [], bagShape), ["kind", "source"]);
  assert.deepEqual(openOf({ ...bare, heavy: 1 }, [], bagShape), ["kind", "handling"]);
  assert.deepEqual(openOf({ ...bare, open_first: 1 }, [], bagShape), ["kind", "handling"]);
  assert.deepEqual(openOf(bare, [{ id: 1, name: "forks" }], bagShape), ["kind", "items"]);
});

test("whitespace is not content", () => {
  assert.deepEqual(openOf({ ...bare, content_summary: "   " }, [], bagShape), ["kind"]);
});

test("a single thing has no size and nothing inside it to list", () => {
  const lamp = { ...bare, kind: "item", content_summary: "Desk lamp" };
  assert.deepEqual(sectionsOf(lamp, [], lampShape), ["summary", "kind", "source", "handling"]);
  assert.deepEqual(openOf(lamp, [], lampShape), ["summary", "kind"]);
});

test("the summary is named for what the record is", () => {
  const named = (shape) => editorSections(bare, [], shape).find((s) => s.key === "summary").legend;
  assert.equal(named(bagShape), "What is in it");
  assert.equal(named(lampShape), "What it is");
});

// --- the photos in the sub-item editor ------------------------------------------
//
// Not an input, so there is nothing to fold *open* to: a record with no photos
// has no photo section at all, the way a lamp has no size.

test("a record with photos shows them, last, and open", () => {
  const seen = editorSections(bare, [], bagShape, [{ id: 1 }, { id: 2 }]);
  assert.deepEqual(seen.at(-1), { key: "photos", legend: "Photos", open: true });
});

test("a record with no photos has no photo section at all", () => {
  for (const none of [[], null, undefined]) {
    assert.ok(!editorSections(bare, [], bagShape, none).some((s) => s.key === "photos"));
  }
});

test("photos are not about holding contents: a single thing has them too", () => {
  const lamp = { ...bare, kind: "item" };
  const keys = editorSections(lamp, [], lampShape, [{ id: 1 }]).map((s) => s.key);
  assert.deepEqual(keys, ["summary", "kind", "source", "handling", "photos"]);
});

test("a record the server has not described yet is still all there", () => {
  // Every field missing rather than empty: nothing throws, nothing opens.
  assert.deepEqual(openOf({ code: "B-0009", kind: "bag" }, undefined, bagShape), ["kind"]);
});

// --- the viewfinder in the add dialog ------------------------------------------------
//
// The live camera can fail in half a dozen ordinary ways -- no permission, no
// camera, a plain LAN address -- and none of them is an error state: the file
// picker is still there, and the line says which of them happened.

test("no secure context is the one that is about the address, not the camera", () => {
  // getUserMedia rejects silently on a LAN IP; this is checked before asking.
  const said = cameraTrouble(null, { secure: false });
  assert.match(said, /secure connection/);
  assert.match(said, /[Cc]hoose a photo/);
});

test("a refusal says so plainly, and is not an error", () => {
  const said = cameraTrouble({ name: "NotAllowedError" });
  assert.match(said, /declined/);
  assert.match(said, /[Cc]hoose a photo/);
  assert.doesNotMatch(said, /error|failed/i);
});

test("no camera, and a camera somebody else is using, read differently", () => {
  assert.match(cameraTrouble({ name: "NotFoundError" }), /No camera/);
  assert.match(cameraTrouble({ name: "OverconstrainedError" }), /No camera/);
  assert.match(cameraTrouble({ name: "NotReadableError" }), /already in use|busy/i);
});

test("anything else names itself rather than pretending to know", () => {
  const said = cameraTrouble({ name: "AbortError" });
  assert.match(said, /AbortError/);
  assert.match(said, /[Cc]hoose a photo/);
});

test("every one of them points at the way that still works", () => {
  for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "AbortError", undefined]) {
    assert.match(cameraTrouble(name ? { name } : null), /[Cc]hoose a photo/);
  }
});

// --- what a captured frame comes out as ----------------------------------------------
//
// The server keeps 2048 px at most, so there is no point uploading more.

test("a big frame comes down to what the server would keep", () => {
  assert.deepEqual(frameSize(4032, 3024), { width: 2048, height: 1536 });
  // Held upright, the long edge is the height.
  assert.deepEqual(frameSize(3024, 4032), { width: 1536, height: 2048 });
});

test("a small frame is left alone rather than blown up", () => {
  assert.deepEqual(frameSize(640, 480), { width: 640, height: 480 });
  assert.deepEqual(frameSize(1080, 1920), { width: 1080, height: 1920 });
  assert.deepEqual(frameSize(2048, 1536), { width: 2048, height: 1536 });
});

test("the shape is kept, to whole pixels", () => {
  const { width, height } = frameSize(3000, 2001);
  assert.equal(width, 2048);
  assert.equal(height, Math.round(2001 * (2048 / 3000)));
  assert.ok(Number.isInteger(height));
});

test("a frame with no size yet is not a frame", () => {
  // The video element has no dimensions until it has data.
  for (const bad of [[0, 0], [640, 0], [Number.NaN, 480]]) {
    assert.equal(frameSize(...bad), null);
  }
});

// --- search results read as a tree -------------------------------------------------
//
// The container first, what matched inside it indented under it. Search is the
// one view that looks inside containers, so the rule that arranges it must not
// hide a match, drop one, or show one twice.

const hit = (code, ancestry = []) => ({ code, ancestry, matched: true });
const around = (code, ancestry = []) => ({ code, ancestry, matched: false });
const shown = (rows) => groupMatches(rows).map((r) => `${" ".repeat(r.depth)}${r.row.code}${r.context ? "*" : ""}`);

test("matches that are inside nothing are a flat list, in the order they came", () => {
  // Relevance order, which is what search sorted them into.
  assert.deepEqual(shown([hit("B-0003"), hit("B-0001"), hit("B-0002")]),
                   ["B-0003", "B-0001", "B-0002"]);
});

test("the container comes first and what matched inside it is indented under it", () => {
  const rows = [hit("B-0002", ["B-0001"]), hit("B-0003", ["B-0001"]), around("B-0001")];

  // The crate is context -- it did not match -- and both bags sit under it.
  assert.deepEqual(shown(rows), ["B-0001*", " B-0002", " B-0003"]);
});

test("a whole chain is walked, each step one deeper", () => {
  const rows = [hit("B-0004", ["B-0001", "B-0002", "B-0003"]),
                around("B-0001"), around("B-0002", ["B-0001"]),
                around("B-0003", ["B-0001", "B-0002"])];

  assert.deepEqual(shown(rows), ["B-0001*", " B-0002*", "  B-0003*", "   B-0004"]);
});

test("a container that matched as well appears once, as a match and not as context", () => {
  // The case that would show a record twice: it is both a result and the
  // place another result lives.
  const rows = [hit("B-0001"), hit("B-0002", ["B-0001"])];

  assert.deepEqual(shown(rows), ["B-0001", " B-0002"]);
});

test("nothing is dropped: every row comes out exactly once", () => {
  const rows = [hit("B-0004", ["B-0001", "B-0002"]), hit("B-0001"), around("B-0002", ["B-0001"]),
                hit("B-0009"), hit("B-0005", ["B-0001"])];

  const out = groupMatches(rows);

  assert.equal(out.length, rows.length);
  assert.deepEqual(out.map((r) => r.row.code).sort(), rows.map((r) => r.code).sort());
});

test("groups keep the order of the best match inside them", () => {
  // B-0009 matched before anything in the crate did, so it stays above it.
  const rows = [hit("B-0009"), hit("B-0003", ["B-0001"]), around("B-0001")];

  assert.deepEqual(shown(rows), ["B-0009", "B-0001*", " B-0003"]);
});

test("an ancestor that is not in the results does not leave a gap", () => {
  // Defensive: depth counts the steps actually shown, so a missing one cannot
  // push a row off the right of a phone.
  assert.deepEqual(shown([hit("B-0004", ["B-0404", "B-0002"]), around("B-0002")]),
                   ["B-0002*", " B-0004"]);
});

test("nothing found is nothing shown", () => {
  assert.deepEqual(groupMatches([]), []);
  assert.deepEqual(groupMatches(null), []);
});

test("a list that flags nothing is all matches, flat", () => {
  // Browsing and a container's contents carry no `matched` at all, so one
  // path can draw every list there is.
  assert.deepEqual(shown([{ code: "B-0001" }, { code: "B-0002" }]), ["B-0001", "B-0002"]);
});

test("a row is handed back whole, so the list draws it as it draws any row", () => {
  const row = { code: "B-0002", ancestry: ["B-0001"], matched: true, kind: "bag", content_summary: "cutlery" };

  const [, under] = groupMatches([row, around("B-0001")]);

  assert.equal(under.row, row);
  assert.equal(under.depth, 1);
  assert.equal(under.context, false);
});
