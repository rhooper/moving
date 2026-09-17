// Splitting a dictated or typed list into items.
import assert from "node:assert/strict";
import { test } from "node:test";

import { splitItems } from "../web/text.js";

test("splits a comma list", () => {
  assert.deepEqual(splitItems("kettle, toaster, mugs"), ["kettle", "toaster", "mugs"]);
});

test("splits on newlines, for typing one per line", () => {
  assert.deepEqual(splitItems("kettle\ntoaster\n\nmugs"), ["kettle", "toaster", "mugs"]);
});

test("drops the 'and' a dictated list ends with", () => {
  // Speech engines punctuate however they like; "kettle, toaster and mugs"
  // must not produce an item literally called "and mugs".
  assert.deepEqual(splitItems("kettle, toaster, and mugs"), ["kettle", "toaster", "mugs"]);
  assert.deepEqual(splitItems("kettle; then mugs"), ["kettle", "mugs"]);
});

test("keeps quantities inside the item text", () => {
  assert.deepEqual(splitItems("three mugs, two pans"), ["three mugs", "two pans"]);
});

test("ignores empty input and stray punctuation", () => {
  assert.deepEqual(splitItems(""), []);
  assert.deepEqual(splitItems("   "), []);
  assert.deepEqual(splitItems(null), []);
  assert.deepEqual(splitItems(",,, ,"), []);
});

test("caps a runaway dictation", () => {
  const many = Array.from({ length: 200 }, (_, i) => `thing ${i}`).join(", ");
  assert.equal(splitItems(many).length, 50);
});
