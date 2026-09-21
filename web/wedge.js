// A keyboard-wedge barcode reader types what it scans and presses Return: the
// label's Code 128 carries the box number, its QR the box URL. No DOM; app.js
// wires it up.

import { codeFrom } from "./scan.js";

// The box entered text points at, or null. `scanned`: a box URL can only come
// from a label, so it opens without asking; a bare word is only *shaped* like a
// code (so is "kettle"), and the caller looks it up first.
export function entered(text) {
  const raw = String(text ?? "").trim();
  const code = codeFrom(raw);
  if (!code) return null;
  return { code, scanned: /^https?:\/\//i.test(raw) };
}

// Collects keys that arrive while nothing is focused, which would otherwise go nowhere.
export class KeyBuffer {
  constructor({ idle = 1000 } = {}) {
    this.idle = idle;   // ms of silence after which whatever was collected is stale
    this.text = "";
    this.last = 0;
  }

  // The page keeps "/" and "'" from Firefox's quick find while this is true.
  collecting(now) {
    return this.text !== "" && now - this.last <= this.idle;
  }

  // Feed one keydown's `key`. Returns the collected text on Enter, else null.
  feed(key, now) {
    if (!this.collecting(now)) this.text = "";
    if (key === "Enter") {
      const done = this.text;
      this.text = "";
      return done || null;
    }
    // Named keys ("Shift", "ArrowDown") are longer than one character.
    if (key.length === 1) {
      this.text += key;
      this.last = now;
    }
    return null;
  }
}
