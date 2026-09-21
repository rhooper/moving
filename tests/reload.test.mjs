// Keeping an open page up with deploys: which revision it is running, what the
// server says is live, whether a reload is wanted, whether one is safe *now*,
// and -- the guard -- whether this page has already reloaded itself towards
// that revision once. Away from the DOM, because a reload is the one action a
// browser check can only watch happen once per page.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHECK_MS,
  liveRevision,
  nextStep,
  readTried,
  rememberTried,
  reloadBlocked,
  runningRevision,
} from "../web/reload.js";

// --- which revision this page is running ------------------------------------

test("the running revision is the ?v= every module was served with", () => {
  assert.equal(runningRevision("https://moving.example/app.js?v=abc1234"), "abc1234");
});

test("a dev server serves modules unversioned, and then there is nothing to compare", () => {
  // Off in dev, or a dev server reloads itself forever.
  assert.equal(runningRevision("http://127.0.0.1:8787/app.js"), null);
});

test("'unknown' is not a revision, whoever wrote it into a URL", () => {
  assert.equal(runningRevision("http://127.0.0.1:8787/app.js?v=unknown"), null);
  assert.equal(runningRevision("http://127.0.0.1:8787/app.js?v="), null);
});

test("something that is not a URL at all is not a revision either", () => {
  assert.equal(runningRevision("not a url"), null);
  assert.equal(runningRevision(undefined), null);
});

// --- what the server says is live --------------------------------------------

test("the live revision is /health's", () => {
  assert.equal(liveRevision({ status: "ok", revision: "def5678", version: "0.2.41" }), "def5678");
});

test("a /health that could not be read says nothing -- never 'changed'", () => {
  // During a deploy the service is briefly down; a failed check is "cannot tell".
  for (const said of [null, undefined, {}, { revision: "" }, { revision: 7 }, "oops"]) {
    assert.equal(liveRevision(said), null, JSON.stringify(said));
  }
});

test("a server that does not know its revision is not reporting a new one", () => {
  assert.equal(liveRevision({ revision: "unknown" }), null);
});

// --- whether a reload is wanted ---------------------------------------------

test("a dev page never reloads, whatever the server says", () => {
  assert.equal(nextStep({ running: null, live: "B", tried: null }), "off");
});

test("a check that failed changes nothing", () => {
  assert.equal(nextStep({ running: "A", live: null, tried: null }), "wait");
});

test("the same revision is current", () => {
  assert.equal(nextStep({ running: "A", live: "A", tried: null }), "current");
});

test("a new revision, never tried, is reloaded towards", () => {
  assert.equal(nextStep({ running: "A", live: "B", tried: null }), "reload");
});

test("a revision already reloaded towards once is only offered, never reloaded again", () => {
  // The reload landed on the old code -- a stale shell -- and /health still
  // says B. Reloading again would do the same thing again, forever.
  assert.equal(nextStep({ running: "A", live: "B", tried: "B" }), "offer");
});

test("an older attempt does not stand in the way of the next deploy", () => {
  assert.equal(nextStep({ running: "B", live: "C", tried: "B" }), "reload");
  assert.equal(nextStep({ running: "A", live: "C", tried: "B" }), "reload");
});

test("with nowhere to remember an attempt, a reload is only offered", () => {
  // `tried` is undefined when sessionStorage cannot be read: this page could
  // not tell its second reload from its first, so it does not start one.
  assert.equal(nextStep({ running: "A", live: "B", tried: undefined }), "offer");
});

test("no loop: however often it checks, one deploy is one reload", () => {
  // Every reload lands on a stale shell, still running A; /health keeps
  // saying B. A page that reloaded each time would never stop.
  const storage = memory();
  let reloads = 0;
  for (let check = 0; check < 50; check++) {
    const step = nextStep({ running: "A", live: "B", tried: readTried(storage) });
    if (step === "reload") {
      rememberTried(storage, "B");
      reloads += 1;
    }
  }
  assert.equal(reloads, 1);
});

test("and a real landing is simply current, with the next deploy still wanted", () => {
  const storage = memory();
  assert.equal(nextStep({ running: "A", live: "B", tried: readTried(storage) }), "reload");
  rememberTried(storage, "B");
  // The reload landed on B.
  assert.equal(nextStep({ running: "B", live: "B", tried: readTried(storage) }), "current");
  assert.equal(nextStep({ running: "B", live: "C", tried: readTried(storage) }), "reload");
});

// --- remembering the attempt --------------------------------------------------

test("nothing tried yet reads as null", () => {
  assert.equal(readTried(memory()), null);
});

test("an attempt is remembered", () => {
  const storage = memory();
  assert.equal(rememberTried(storage, "B"), true);
  assert.equal(readTried(storage), "B");
});

test("storage that throws reads as undefined, and says it could not remember", () => {
  // A private window, blocked site data: the accessor itself can throw.
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(readTried(broken), undefined);
  assert.equal(rememberTried(broken, "B"), false);
  assert.equal(readTried(undefined), undefined);
  assert.equal(rememberTried(undefined, "B"), false);
});

// --- whether a reload is safe now -------------------------------------------

test("an idle page may reload", () => {
  assert.equal(reloadBlocked({}), false);
  assert.equal(reloadBlocked(undefined), false);
});

test("a page the refresh would hold back for is held back for a reload too", () => {
  // `held` is holdRefresh's own answer -- a focused or dirty field, a finger
  // on the glass, the settle after one -- not a second copy of it.
  assert.equal(reloadBlocked({ held: true }, "idle"), true);
});

test("but not when the person asked, or when the page is being left anyway", () => {
  // Tapping the banner is itself a touch, and leaving a record already
  // discards what is on screen: neither is a thumb that a reload would move.
  assert.equal(reloadBlocked({ held: true }, "asked"), false);
  assert.equal(reloadBlocked({ held: true }, "leaving"), false);
});

test("an open dialog blocks it however it was started", () => {
  // The add-inside dialog may have the camera live, and the photo viewer may
  // be over a modal. A reload discards both.
  for (const moment of ["idle", "asked", "leaving"]) {
    assert.equal(reloadBlocked({ dialog: true }, moment), true, moment);
  }
});

test("so does a request still out, and a save that has not landed", () => {
  for (const moment of ["idle", "asked", "leaving"]) {
    assert.equal(reloadBlocked({ busy: true }, moment), true, moment);
    assert.equal(reloadBlocked({ owed: true }, moment), true, moment);
  }
});

test("the periodic check is a backstop, measured in minutes' worth of seconds", () => {
  // Not a tight poll: the fast paths (visible again, socket reconnected)
  // catch almost every deploy first.
  assert.ok(CHECK_MS >= 30000 && CHECK_MS <= 300000, String(CHECK_MS));
});

function memory() {
  const held = new Map();
  return {
    getItem: (key) => (held.has(key) ? held.get(key) : null),
    setItem: (key, value) => { held.set(key, String(value)); },
  };
}
