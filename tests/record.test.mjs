// The read-only record sheet: which pictures it shows, in what order, where it
// stops, and what the four lines under them say. Someone has just scanned a
// sealed box and is holding it, so the rules are about answering "what is
// this?" in one screen without being able to change anything.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COVERS,
  contentsLine,
  coverTiles,
  expandedGroups,
  facts,
} from "../web/record.js";

const child = (code) => ({ code, kind: "tub" });
const children = (n) => Array.from({ length: n }, (_, i) => child(`B-${String(i + 1).padStart(4, "0")}`));
const named = (names) => names.map((name, id) => ({ id, name, qty: 1 }));

// --- the covers of what is inside -------------------------------------------

test("a few things inside are all shown", () => {
  const { tiles, more } = coverTiles(children(3));

  assert.deepEqual(tiles.map((t) => t.code), ["B-0001", "B-0002", "B-0003"]);
  assert.equal(more, 0);
});

test("exactly a screenful is still all shown, with nothing left over", () => {
  const { tiles, more } = coverTiles(children(COVERS));

  assert.equal(tiles.length, COVERS);
  assert.equal(more, 0);
});

test("more than that keeps room for the tile that opens the rest", () => {
  // B-0015 holds 20 tubs: the grid stays one tidy block, and the last cell
  // says how many are behind it rather than being a twenty-first tub.
  const { tiles, more } = coverTiles(children(20));

  assert.equal(tiles.length, COVERS - 1);
  assert.equal(more, 20 - (COVERS - 1));
  assert.deepEqual(tiles.at(-1).code, "B-0007");
});

test("show all means all of them, and nothing left to open", () => {
  const { tiles, more } = coverTiles(children(20), { all: true });

  assert.equal(tiles.length, 20);
  assert.equal(more, 0);
});

test("nothing inside is no tiles", () => {
  assert.deepEqual(coverTiles([]), { tiles: [], more: 0 });
  assert.deepEqual(coverTiles(null), { tiles: [], more: 0 });
});

// --- the contents line -------------------------------------------------------

test("the contents line counts, then names a few", () => {
  const line = contentsLine(named(["LEDs", "resistors", "capacitors", "nixie tubes", "a soldering iron"]));

  assert.equal(line.value, "5 items");
  assert.equal(line.detail, "LEDs, resistors, capacitors, nixie tubes…");
  assert.equal(line.expandable, true);
});

test("a short list is named in full, with nothing trailing", () => {
  const line = contentsLine(named(["kettle", "toaster"]));

  assert.equal(line.value, "2 items");
  assert.equal(line.detail, "kettle, toaster");
});

test("one item is one item", () => {
  assert.equal(contentsLine(named(["kettle"])).value, "1 item");
});

test("a record with nothing listed but something inside can still be opened", () => {
  // The crate lists nothing of its own; the tubs in it list plenty.
  const line = contentsLine([], { children: children(3) });

  assert.equal(line.value, "Nothing listed on this one");
  assert.equal(line.detail, "");
  assert.equal(line.expandable, true);
});

test("a record with nothing listed and nothing inside has nothing to open", () => {
  const line = contentsLine([], { children: [] });

  assert.equal(line.value, "Nothing listed");
  assert.equal(line.expandable, false);
});

// --- the facts ---------------------------------------------------------------

const box = {
  status: "packed", kind: "box", size: "medium",
  current_location: "", source_location: "shelf 3",
};

test("the facts are status, what it is, what is in it, where it was packed from", () => {
  const lines = facts(box, {
    what: "medium box",
    room: { name: "Basement" },
    contents: contentsLine(named(["LEDs", "resistors", "capacitors"])),
  });

  assert.deepEqual(lines.map((f) => f.key), ["status", "what", "contents", "from"]);
  assert.deepEqual(lines.map((f) => f.label), ["Status", "What", "Contents", "Packed from"]);
});

test("the status is a word, set as one", () => {
  const [status] = facts(box, { what: "medium box" });

  assert.equal(status.value, "Packed");
  assert.equal(status.pill, true);
});

test("what it is reads as a sentence, not as a cell of a table", () => {
  // rowStatus makes the phrase, so a row and this page cannot disagree.
  const what = facts(box, { what: "XL crate" }).find((f) => f.key === "what");

  assert.equal(what.value, "XL crate");
});

test("where it is right now is its own line, and only when there is one", () => {
  // A box for the kitchen may be on the truck: the two are different things.
  const quiet = facts(box, { what: "medium box" });
  assert.equal(quiet.some((f) => f.key === "now"), false);

  const moving = facts({ ...box, current_location: "the truck" }, { what: "medium box" });
  const now = moving.find((f) => f.key === "now");
  assert.equal(now.value, "the truck");
  // Straight after the status, which it qualifies.
  assert.deepEqual(moving.map((f) => f.key).slice(0, 2), ["status", "now"]);
});

test("packed from names the room and the place in it", () => {
  const from = facts({ ...box, source_location: "shelf 3" }, {
    what: "medium box", source: { name: "Basement" },
  }).find((f) => f.key === "from");

  assert.equal(from.value, "Basement — shelf 3");
});

test("a room with no place, and a place with no room, each say what they know", () => {
  const roomOnly = facts({ ...box, source_location: "" }, { what: "box", source: { name: "Basement" } });
  assert.equal(roomOnly.find((f) => f.key === "from").value, "Basement");

  const placeOnly = facts({ ...box, source_location: "shelf 3" }, { what: "box" });
  assert.equal(placeOnly.find((f) => f.key === "from").value, "shelf 3");
});

test("nothing recorded about where it came from leaves the line out", () => {
  const lines = facts({ ...box, source_location: "" }, { what: "box" });

  assert.deepEqual(lines.map((f) => f.key), ["status", "what"]);
});

test("a single thing has no contents line at all", () => {
  // A lamp holds nothing; "Contents: nothing listed" would be a question
  // nobody asked.
  const lines = facts({ ...box, kind: "item" }, { what: "loose item", contents: null });

  assert.deepEqual(lines.map((f) => f.key), ["status", "what", "from"]);
});

// --- the expanded contents ---------------------------------------------------

test("one record's items need no heading: there is nowhere else they could be", () => {
  const groups = expandedGroups([{ code: "B-0042", items: named(["kettle", "toaster"]) }]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].heading, false);
});

test("with anything nested, every group is headed by its code -- its own too", () => {
  const groups = expandedGroups([
    { code: "B-0015", items: named(["resistors"]) },
    { code: "B-0016", items: named(["a soldering iron"]) },
  ]);

  assert.deepEqual(groups.map((g) => g.code), ["B-0015", "B-0016"]);
  assert.deepEqual(groups.map((g) => g.heading), [true, true]);
});

test("a group with one item is still headed, which is the whole point", () => {
  const groups = expandedGroups([
    { code: "B-0015", items: named(["resistors", "LEDs"]) },
    { code: "B-0016", items: named(["a soldering iron"]) },
  ]);

  assert.equal(groups[1].items.length, 1);
  assert.equal(groups[1].heading, true);
});

test("a record with nothing listed is not an empty heading", () => {
  const groups = expandedGroups([
    { code: "B-0015", items: named(["resistors"]) },
    { code: "B-0016", items: [] },
  ]);

  assert.deepEqual(groups.map((g) => g.code), ["B-0015"]);
});

test("nothing anywhere is no groups", () => {
  assert.deepEqual(expandedGroups([]), []);
  assert.deepEqual(expandedGroups(null), []);
});

test("the order the server sent is kept: the record, then outwards", () => {
  const groups = expandedGroups([
    { code: "B-0015", items: named(["a"]) },
    { code: "B-0016", items: named(["b"]) },
    { code: "B-0099", items: named(["c"]) },
  ]);

  assert.deepEqual(groups.map((g) => g.code), ["B-0015", "B-0016", "B-0099"]);
});
