// A keyboard-wedge barcode reader "types" what it scans and presses Return.
// The Code 128 on a label carries the box number; the QR carries the box's
// URL. Either one, entered in the search box or typed at the page with nothing
// focused, should open that box.
//
// Pure logic only, so it can be tested without a browser; app.js wires it up.

import { codeFrom } from "./scan.js";

// What a piece of entered text points at, or null if it is just text.
//
// `scanned` says how sure we can be. A box URL can only have come from a label,
// so it is opened without asking. A bare number is only *shaped* like a code --
// so is "kettle" -- and the caller has to look it up before jumping, or every
// one-word search would land on a "no such box" page.
export function entered(text) {
  const raw = String(text ?? "").trim();
  const code = codeFrom(raw);
  if (!code) return null;
  return { code, scanned: /^https?:\/\//i.test(raw) };
}

// Collects keys that arrive while nothing is focused. A wedge reader has no
// idea where the cursor is, and on a page with no field focused its keystrokes
// would otherwise go nowhere.
export class KeyBuffer {
  constructor({ idle = 1000 } = {}) {
    this.idle = idle;   // ms of silence after which whatever was collected is stale
    this.text = "";
    this.last = 0;
  }

  // Whether a run of keys is in progress. The page uses this to keep "/" and
  // "'" away from Firefox's quick find while a URL is being typed in.
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
