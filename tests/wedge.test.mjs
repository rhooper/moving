// A keyboard-wedge barcode reader "types" what it scans and presses Return.
// The Code 128 on a label is the box number; the QR is the box's URL. Either
// one, entered anywhere, should open that box.

import assert from "node:assert/strict";
import { test } from "node:test";

import { KeyBuffer, entered } from "../web/wedge.js";

const HOST = "https://moving.example.ts.net";

// --- what was entered ---------------------------------------------------------

test("a scanned QR is a box URL, and is certain", () => {
  assert.deepEqual(entered(`${HOST}/b/B-0042`), { code: "B-0042", scanned: true });
  assert.deepEqual(entered(`${HOST}/b/CAM-001?from=scan`), { code: "CAM-001", scanned: true });
});

test("a scanned Code 128 is a bare number, and has to be looked up", () => {
  // "KETTLE" is code-shaped too; only the database knows which this is.
  assert.deepEqual(entered("B-0042"), { code: "B-0042", scanned: false });
  assert.deepEqual(entered("  z06-001 "), { code: "Z06-001", scanned: false });
  assert.deepEqual(entered("kettle"), { code: "KETTLE", scanned: false });
});

test("an ordinary search is not a box", () => {
  assert.equal(entered("pots and pans"), null);
  assert.equal(entered(""), null);
  assert.equal(entered("   "), null);
  assert.equal(entered(null), null);
});

test("a URL that is not a box URL is not a box", () => {
  assert.equal(entered("https://example.com/somewhere/else"), null);
  assert.equal(entered(`${HOST}/#/settings`), null);
});

// --- keys arriving with nothing focused -----------------------------------------

function typed(buffer, text, { start = 0, gap = 5 } = {}) {
  let at = start;
  let result = null;
  for (const key of text) {
    result = buffer.feed(key, at);
    at += gap;
  }
  return { result, at };
}

test("a run of keys ending in Enter is what was scanned", () => {
  const buffer = new KeyBuffer();
  const { at } = typed(buffer, "B-0042");

  assert.equal(buffer.feed("Enter", at), "B-0042");
});

test("a URL comes through whole, punctuation and all", () => {
  const buffer = new KeyBuffer();
  const { at } = typed(buffer, `${HOST}/b/B-0042`);

  assert.equal(buffer.feed("Enter", at), `${HOST}/b/B-0042`);
});

test("Enter on its own is nothing", () => {
  assert.equal(new KeyBuffer().feed("Enter", 0), null);
});

test("keys that are not characters are ignored, not collected", () => {
  const buffer = new KeyBuffer();
  buffer.feed("Shift", 0);
  buffer.feed("B", 5);
  buffer.feed("ArrowDown", 10);
  buffer.feed("1", 15);

  assert.equal(buffer.feed("Enter", 20), "B1");
});

test("a pause starts again, so stray keys do not prefix the next scan", () => {
  const buffer = new KeyBuffer({ idle: 1000 });
  buffer.feed("x", 0);
  const { at } = typed(buffer, "B-0042", { start: 5000 });

  assert.equal(buffer.feed("Enter", at), "B-0042");
});

test("after Enter the buffer is empty again", () => {
  const buffer = new KeyBuffer();
  const first = typed(buffer, "B-0001");
  buffer.feed("Enter", first.at);
  const second = typed(buffer, "B-0002", { start: first.at + 10 });

  assert.equal(buffer.feed("Enter", second.at), "B-0002");
});

test("it knows when it is mid-scan, so the page can keep '/' away from quick find", () => {
  const buffer = new KeyBuffer({ idle: 1000 });
  assert.equal(buffer.collecting(0), false);

  buffer.feed("h", 0);
  assert.equal(buffer.collecting(10), true);
  assert.equal(buffer.collecting(5000), false);
});
