// Things inside things, as the page sees them: the way out of a nested record,
// the words for what is inside, and whether one record may go inside another
// -- decided here, before the server is asked, so the page never offers a move
// the server would refuse.
import assert from "node:assert/strict";
import { test } from "node:test";

import { blockedDelete, describe, mayHold, trail } from "../web/nesting.js";

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
