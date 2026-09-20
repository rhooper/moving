// A row of joined pushbuttons: [ Box | Tub | Crate ]. One is selected; in an
// optional row, pressing the selected one again clears it.
//
// The control is real radio buttons underneath, and a radio does not un-check
// when it is pressed -- that is the whole of what this adds, so the decision is
// here, away from the DOM, where it can be pinned down.
import assert from "node:assert/strict";
import { test } from "node:test";

import { hintFor, pressed } from "../web/segmented.js";

test("pressing a button selects it", () => {
  assert.deepEqual(pressed("", "kitchen", { optional: true }), { value: "kitchen", cleared: false });
  assert.deepEqual(pressed("box", "tub", { optional: false }), { value: "tub", cleared: false });
});

test("pressing another button moves the selection; nothing is cleared on the way", () => {
  assert.deepEqual(pressed("kitchen", "garage", { optional: true }), { value: "garage", cleared: false });
});

test("pressing the selected button of an optional row clears it", () => {
  assert.deepEqual(pressed("kitchen", "kitchen", { optional: true }), { value: "", cleared: true });
});

test("a row that must have a value ignores the second press", () => {
  // The kind. There is no such thing as a record that is not anything.
  assert.deepEqual(pressed("box", "box", { optional: false }), { value: "box", cleared: false });
  assert.deepEqual(pressed("box", "box"), { value: "box", cleared: false });
});

test("values are compared as the strings a form holds", () => {
  // A room id arrives from the API as a number and sits in the radio as "3".
  assert.deepEqual(pressed(3, "3", { optional: true }), { value: "", cleared: true });
  assert.deepEqual(pressed(null, "3", { optional: true }), { value: "3", cleared: false });
});

// --- the line under an optional row ------------------------------------------------
//
// There is no empty <option> any more to say what "nothing chosen" means, and
// pressing a selected button to clear it is not something anyone would guess.
// The line under the row says the one while nothing is chosen and the other
// once something is. It is always there, so neither appearing moves the page.

test("with nothing chosen the line says what that means", () => {
  assert.equal(hintFor("", "Not decided yet"), "Not decided yet");
  assert.equal(hintFor(null, "No size"), "No size");
});

test("with something chosen it says how to un-choose it", () => {
  assert.equal(hintFor("3", "Not decided yet"), "Tap it again to clear");
});
