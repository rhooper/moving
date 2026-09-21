// Scanner code parsing. Run via `node --test tests/`, and by pytest through
// tests/test_scan_js.py so one command covers it.
import assert from "node:assert/strict";
import { test } from "node:test";

import { codeFrom } from "../web/scan.js";

const HOST = "https://moving.example.ts.net";

test("reads every configurable code shape out of a scanned URL", () => {
  assert.equal(codeFrom(`${HOST}/b/B-0042`), "B-0042");
  assert.equal(codeFrom(`${HOST}/b/CAM-001`), "CAM-001");
  assert.equal(codeFrom(`${HOST}/b/D001`), "D001");
  assert.equal(codeFrom(`${HOST}/b/Z06-001`), "Z06-001");
  assert.equal(codeFrom(`${HOST}/b/BOX_01`), "BOX_01");
});

test("accepts a bare code, for anything printed differently", () => {
  assert.equal(codeFrom("CAM-001"), "CAM-001");
  assert.equal(codeFrom("  d001  "), "D001");
});

test("ignores the host, so a label survives the server moving", () => {
  assert.equal(codeFrom("https://elsewhere.example/b/CAM-001"), "CAM-001");
  assert.equal(codeFrom("http://127.0.0.1:8787/b/CAM-001"), "CAM-001");
});

test("tolerates query strings and a trailing slash", () => {
  assert.equal(codeFrom(`${HOST}/b/B-0042?from=scan&ref=truck`), "B-0042");
  assert.equal(codeFrom(`${HOST}/b/B-0042/`), "B-0042");
  assert.equal(codeFrom(`${HOST}/b/B-0042#/x`), "B-0042");
});

test("refuses an unrelated QR code rather than inventing a box", () => {
  // Without the /b/ marker this would hand back "generic-widget" and send the
  // app looking up a box that never existed.
  assert.equal(codeFrom("https://example.com/products/generic-widget"), null);
  assert.equal(codeFrom("https://example.com/"), null);
  assert.equal(codeFrom(`${HOST}/b/`), null);
});

test("refuses junk", () => {
  assert.equal(codeFrom(""), null);
  assert.equal(codeFrom("   "), null);
  assert.equal(codeFrom(null), null);
  assert.equal(codeFrom("WIFI:S:home;T:WPA;P:hunter2;;"), null);
});
