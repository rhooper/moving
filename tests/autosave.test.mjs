// Autosave with undo, in place of Save and Cancel buttons.
//
// The logic is DOM-free so it can be tested here: time is injected, and "save"
// is whatever async function the page hands in.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Autosaver, lineFor, policyFor, retryAfter } from "../web/autosave.js";

// --- a clock we control -----------------------------------------------------------

function fakeTimers() {
  let now = 0;
  let next = 1;
  const timers = new Map();
  return {
    setTimer: (fn, ms) => { const id = next++; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimer: (id) => timers.delete(id),
    advance(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) { timers.delete(id); timer.fn(); }
      }
    },
    pending: () => timers.size,
  };
}

// A save we can watch, hold open, and fail.
function recorder() {
  const calls = [];
  let gate = null;
  let failing = false;
  const save = async (key, value) => {
    calls.push([key, value]);
    if (gate) await gate.promise;
    if (failing) throw new Error("offline");
  };
  return {
    save,
    calls,
    hold() { let release; const promise = new Promise((r) => (release = r)); gate = { promise, release }; },
    release() { const g = gate; gate = null; g.release(); },
    fail(on = true) { failing = on; },
  };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

function saver(extra = {}) {
  const timers = fakeTimers();
  const rec = recorder();
  const events = [];
  const auto = new Autosaver({
    save: rec.save,
    delay: 1200,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    onState: (key, state) => events.push([key, state]),
    ...extra,
  });
  return { auto, timers, rec, events };
}

// --- when to save ---------------------------------------------------------------------

test("a picker saves the moment it changes", () => {
  assert.equal(policyFor({ tagName: "SELECT" }), "change");
  assert.equal(policyFor({ tagName: "INPUT", type: "checkbox" }), "change");
});

test("so does a row of pushbuttons, which is a picker you can see all of", () => {
  // The kind, the size and the two rooms are radio groups now. Left to the
  // default they would be "text": a tap would wait 1.2 s before it saved, and
  // a second tap to clear inside that pause would send nothing at all.
  assert.equal(policyFor({ tagName: "INPUT", type: "radio" }), "change");
  assert.equal(policyFor({ tagName: "INPUT", type: "radio", dataset: {} }), "change");
});

test("text saves after a pause in typing", () => {
  assert.equal(policyFor({ tagName: "TEXTAREA" }), "pause");
  assert.equal(policyFor({ tagName: "INPUT", type: "text" }), "pause");
});

test("a field can insist on being committed: no saving mid-word", () => {
  // The current location writes to the box's history on every save.
  assert.equal(policyFor({ tagName: "INPUT", type: "text", dataset: { autosave: "commit" } }), "commit");
});

// --- pausing -------------------------------------------------------------------------------

test("typing does not save until the typing stops", async () => {
  const { auto, timers, rec } = saver();
  auto.track("summary", "pots");

  auto.edit("summary", "pots a", "pause");
  timers.advance(500);
  auto.edit("summary", "pots and pans", "pause");
  timers.advance(1199);
  assert.deepEqual(rec.calls, []);

  timers.advance(1);
  await settle();
  assert.deepEqual(rec.calls, [["summary", "pots and pans"]]);
});

test("leaving the field saves at once", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pots and pans", "pause");

  auto.commit("summary");
  await settle();

  assert.deepEqual(rec.calls, [["summary", "pots and pans"]]);
});

test("leaving a field that was not changed saves nothing", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");

  auto.commit("summary");
  await settle();

  assert.deepEqual(rec.calls, []);
});

test("typing a change and typing it back saves nothing", async () => {
  const { auto, timers, rec } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pot", "pause");
  auto.edit("summary", "pots", "pause");

  timers.advance(5000);
  await settle();

  assert.deepEqual(rec.calls, []);
});

test("a commit-only field ignores pauses", async () => {
  const { auto, timers, rec } = saver();
  auto.track("where", "garage");
  auto.edit("where", "garage st", "commit");

  timers.advance(60000);
  await settle();
  assert.deepEqual(rec.calls, []);

  auto.edit("where", "garage stack 3", "commit");
  auto.commit("where");
  await settle();
  assert.deepEqual(rec.calls, [["where", "garage stack 3"]]);
});

test("a picker saves without waiting", async () => {
  const { auto, rec } = saver();
  auto.track("room", "");

  auto.edit("room", "3", "change");
  await settle();

  assert.deepEqual(rec.calls, [["room", "3"]]);
});

// --- saves overlapping -----------------------------------------------------------------------

test("an edit made while a save is in flight is saved afterwards, once", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  rec.hold();
  auto.edit("summary", "pots and", "change");
  await settle();

  auto.edit("summary", "pots and p", "change");
  auto.edit("summary", "pots and pans", "change");
  rec.release();
  await settle();
  await settle();

  assert.deepEqual(rec.calls, [["summary", "pots and"], ["summary", "pots and pans"]]);
});

// --- being told how it went --------------------------------------------------------------------

test("the page hears saving, then saved", async () => {
  const { auto, events } = saver();
  auto.track("summary", "pots");

  auto.edit("summary", "pans", "change");
  await settle();

  assert.deepEqual(events, [["summary", "saving"], ["summary", "saved"]]);
});

test("a failed save says so, keeps the edit, and is retried on the next touch", async () => {
  const { auto, rec, events } = saver();
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  assert.deepEqual(events.at(-1), ["summary", "failed"]);
  assert.equal(auto.unsaved("summary"), true);

  rec.fail(false);
  auto.commit("summary");
  await settle();

  assert.deepEqual(events.at(-1), ["summary", "saved"]);
  assert.equal(auto.unsaved("summary"), false);
});

test("nothing is unsaved once the pause has run out and the save has landed", async () => {
  const { auto, timers } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "pause");
  assert.equal(auto.unsaved("summary"), true);

  timers.advance(1200);
  await settle();

  assert.equal(auto.unsaved("summary"), false);
  assert.equal(auto.unsaved(), false);
});

// --- undo ------------------------------------------------------------------------------------------

test("a save can be undone: the old value goes back to the server", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();

  const undone = await auto.undo();

  assert.deepEqual(undone, { key: "summary", value: "pots" });
  assert.deepEqual(rec.calls.at(-1), ["summary", "pots"]);
});

test("undo walks back newest first, across fields", async () => {
  const { auto } = saver();
  auto.track("summary", "pots");
  auto.track("room", "");
  auto.edit("summary", "pans", "change");
  await settle();
  auto.edit("room", "3", "change");
  await settle();

  assert.deepEqual(await auto.undo(), { key: "room", value: "" });
  assert.deepEqual(await auto.undo(), { key: "summary", value: "pots" });
  assert.equal(await auto.undo(), null);
});

test("it says what the next undo would undo, or that there is nothing", async () => {
  const { auto } = saver();
  auto.track("summary", "pots");
  assert.equal(auto.canUndo(), null);

  auto.edit("summary", "pans", "change");
  await settle();

  assert.equal(auto.canUndo(), "summary");
});

test("undoing is not itself something to undo", async () => {
  const { auto } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();

  await auto.undo();

  assert.equal(auto.canUndo(), null);
});

test("an edit after an undo carries on from the undone value", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();
  await auto.undo();

  auto.edit("summary", "pots, lids", "change");
  await settle();

  assert.deepEqual(rec.calls.at(-1), ["summary", "pots, lids"]);
  assert.deepEqual(await auto.undo(), { key: "summary", value: "pots" });
});

test("a failed undo keeps its place, so it can be tried again", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();
  rec.fail();

  await assert.rejects(() => auto.undo());

  assert.equal(auto.canUndo(), "summary");
});

test("a value the server changed is the new baseline, not an edit", async () => {
  // A live update rewrote a pristine field (the autogenerated summary, say).
  const { auto, timers, rec } = saver();
  auto.track("summary", "");

  auto.track("summary", "kettle, 3 mugs");
  timers.advance(5000);
  await settle();

  assert.deepEqual(rec.calls, []);
  assert.equal(auto.unsaved("summary"), false);
});

test("the undo stack is bounded", async () => {
  const { auto } = saver({ depth: 3 });
  auto.track("n", "0");
  for (const value of ["1", "2", "3", "4", "5"]) {
    auto.edit("n", value, "change");
    await settle();
  }

  const undone = [];
  for (let step = await auto.undo(); step; step = await auto.undo()) undone.push(step.value);

  assert.deepEqual(undone, ["4", "3", "2"]);
});

// --- what the page needs to draw a field's state ---------------------------------------
//
// The record page is redrawn under an open editing session (a handling chip, a
// print, a change of kind), and the saver outlives the fields. So the page has
// to be able to ask, after the fact, what state a field is in and what text it
// was holding -- an event it missed while the old fields were on screen is no
// use to the new ones.

test("a field's state can be asked for at any time", async () => {
  const { auto, timers, rec } = saver();
  auto.track("summary", "pots");
  assert.equal(auto.state("summary"), "clean");

  auto.edit("summary", "pans", "pause");
  assert.equal(auto.state("summary"), "unsaved");

  rec.hold();
  timers.advance(1200);
  await settle();
  assert.equal(auto.state("summary"), "saving");

  rec.release();
  await settle();
  assert.equal(auto.state("summary"), "clean");
});

test("a failed field says failed until it is saved, even while being retyped", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  assert.equal(auto.state("summary"), "failed");

  // Typing on does not make the failure go away; only a save does.
  auto.edit("summary", "pans and lids", "commit");
  assert.equal(auto.state("summary"), "failed");

  rec.fail(false);
  auto.commit("summary");
  await settle();
  assert.equal(auto.state("summary"), "clean");
});

test("a field nobody is tracking has no state", () => {
  const { auto } = saver();
  assert.equal(auto.state("nonsense"), null);
  assert.equal(auto.value("nonsense"), undefined);
});

test("the text a field was holding can be read back, to survive a redraw", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  assert.equal(auto.value("summary"), "pots");

  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  // The save failed and the page is about to be redrawn from the server,
  // which still says "pots". This is what goes back in the field.
  assert.equal(auto.value("summary"), "pans");
});

// --- retrying by itself ------------------------------------------------------------------
//
// "Not saved yet -- will retry" has to be true with nobody touching anything:
// the phone walked out of wifi range mid-sentence and was put in a pocket.

test("a failed save is retried on its own, after a wait", async () => {
  const { auto, timers, rec, events } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  assert.equal(rec.calls.length, 1);

  rec.fail(false);
  timers.advance(1999);
  await settle();
  assert.equal(rec.calls.length, 1, "retried too soon");

  timers.advance(1);
  await settle();
  assert.deepEqual(rec.calls.at(-1), ["summary", "pans"]);
  assert.deepEqual(events.at(-1), ["summary", "saved"]);
  assert.equal(timers.pending(), 0);
});

test("each retry is told how many have failed before it, so the wait can grow", async () => {
  const asked = [];
  const { auto, timers, rec } = saver({ retryDelay: (attempt) => { asked.push(attempt); return 1000; } });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  timers.advance(1000);
  await settle();
  timers.advance(1000);
  await settle();

  assert.deepEqual(asked, [0, 1, 2]);
  assert.equal(rec.calls.length, 3);
});

test("the count of failures starts again after a success", async () => {
  const asked = [];
  const { auto, timers, rec } = saver({ retryDelay: (attempt) => { asked.push(attempt); return 1000; } });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  rec.fail(false);
  timers.advance(1000);
  await settle();

  rec.fail();
  auto.edit("summary", "lids", "change");
  await settle();

  assert.deepEqual(asked, [0, 0]);
});

test("by default the wait doubles and is capped", async () => {
  const waits = [];
  const timers = fakeTimers();
  const rec = recorder();
  const auto = new Autosaver({
    save: rec.save,
    setTimer: (fn, ms) => { waits.push(ms); return timers.setTimer(fn, ms); },
    clearTimer: timers.clearTimer,
  });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  for (let n = 0; n < 6; n += 1) {
    timers.advance(60000);
    await settle();
  }

  assert.deepEqual(waits, [2000, 4000, 8000, 16000, 30000, 30000, 30000]);
});

test("typing again calls the retry off: a retry must not save mid-word either", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("where", "garage");
  rec.fail();
  auto.edit("where", "attic", "commit");
  auto.commit("where");
  await settle();
  assert.equal(rec.calls.length, 1);

  // Back in the field, half a word in, when the retry would have come round.
  // The location writes to the box's history: "attic, by the h" is not a
  // place it has been. Leaving the field is what sends it, as ever.
  rec.fail(false);
  auto.edit("where", "attic, by the h", "commit");
  timers.advance(60000);
  await settle();
  assert.equal(rec.calls.length, 1, "the retry saved a half-typed location");
  assert.equal(auto.state("where"), "failed");

  auto.edit("where", "attic, by the hatch", "commit");
  auto.commit("where");
  await settle();
  assert.deepEqual(rec.calls.at(-1), ["where", "attic, by the hatch"]);
  assert.equal(auto.state("where"), "clean");
});

test("typing again in a field that saves on a pause hands the retry to the pause", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 30000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  rec.fail(false);
  auto.edit("summary", "pans and lids", "pause");
  timers.advance(1200);
  await settle();

  assert.deepEqual(rec.calls.at(-1), ["summary", "pans and lids"]);
  assert.equal(timers.pending(), 0, "the old retry is still waiting to fire");
});

test("saving some other way calls the retry off", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  rec.fail(false);
  auto.commit("summary");
  await settle();
  assert.equal(rec.calls.length, 2);

  timers.advance(60000);
  await settle();
  assert.equal(rec.calls.length, 2, "the retry fired after the save had already landed");
  assert.equal(timers.pending(), 0);
});

test("a new baseline from the server calls the retry off too", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  auto.track("summary", "kettle");
  timers.advance(60000);
  await settle();

  assert.equal(rec.calls.length, 1);
  assert.equal(auto.state("summary"), "clean");
});

test("undoing calls the retry off: the failed edit is what is being given up", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();
  rec.fail();
  auto.edit("summary", "pans and lids", "change");
  await settle();

  rec.fail(false);
  assert.deepEqual(await auto.undo(), { key: "summary", value: "pots" });
  timers.advance(60000);
  await settle();

  assert.deepEqual(rec.calls.at(-1), ["summary", "pots"]);
  assert.equal(auto.state("summary"), "clean");
  assert.equal(timers.pending(), 0);
});

test("a retry that works is undoable like any other save, once", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  rec.fail(false);
  timers.advance(2000);
  await settle();

  assert.deepEqual(await auto.undo(), { key: "summary", value: "pots" });
  assert.equal(await auto.undo(), null);
});

test("the page is told why a save failed", async () => {
  const seen = [];
  const { auto, rec } = saver({ onState: (key, state, error) => seen.push([key, state, error?.message]) });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  assert.deepEqual(seen.at(-1), ["summary", "failed", "offline"]);
});

// --- everything at once ----------------------------------------------------------------------

test("leaving the page saves every field that was waiting", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  auto.track("where", "garage");
  auto.track("room", "");
  auto.edit("summary", "pans", "pause");
  auto.edit("where", "attic", "commit");

  auto.commitAll();
  await settle();

  assert.deepEqual(rec.calls, [["summary", "pans"], ["where", "attic"]]);
});

test("coming back online retries what failed, and only that", async () => {
  const { auto, rec } = saver({ retryDelay: () => 30000 });
  auto.track("summary", "pots");
  auto.track("where", "garage");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();
  // Half a word into a field that must not be saved mid-word.
  auto.edit("where", "garage st", "commit");

  rec.fail(false);
  auto.retryFailed();
  await settle();

  assert.deepEqual(rec.calls.at(-1), ["summary", "pans"]);
  assert.equal(auto.state("summary"), "clean");
  assert.equal(auto.state("where"), "unsaved");
});

// --- what the line under a form says -------------------------------------------------------
//
// One line per form, several fields behind it. What it says is a function of
// where those fields stand and what last happened there -- so it can be
// redrawn from nothing after the page is, and so the wording lives in one place.

test("a form nobody has touched says nothing", () => {
  assert.deepEqual(lineFor([{ state: "clean", policy: "pause" }]), { text: "", warn: false });
  assert.deepEqual(lineFor([]), { text: "", warn: false });
});

test("typing counts as saving: the pause is part of the save, not a wait before it", () => {
  assert.equal(lineFor([{ state: "unsaved", policy: "pause" }]).text, "Saving…");
  assert.equal(lineFor([{ state: "saving", policy: "pause" }]).text, "Saving…");
});

test("a field that saves only when left says how to save it", () => {
  // Otherwise the location looks exactly like a field that is broken: you
  // type, and nothing ever says Saved.
  // Short on purpose: beside an Undo button on a narrow phone, a longer
  // sentence wraps to three lines and pushes the page down as you type.
  assert.equal(lineFor([{ state: "unsaved", policy: "commit" }]).text,
               "Saves when you leave the field");
});

test("a save that landed says so, and an undo says so", () => {
  const clean = [{ state: "clean", policy: "pause" }];
  assert.deepEqual(lineFor(clean, "saved"), { text: "Saved", warn: false });
  assert.deepEqual(lineFor(clean, "undone"), { text: "Undone", warn: false });
});

test("what is happening now outranks what happened last", () => {
  assert.equal(lineFor([{ state: "unsaved", policy: "pause" }], "saved").text, "Saving…");
});

test("a failure is a warning, and outranks everything else in the form", () => {
  const line = lineFor([
    { state: "saving", policy: "change" },
    { state: "failed", policy: "pause" },
    { state: "clean", policy: "change" },
  ], "saved");

  assert.deepEqual(line, { text: "Not saved yet — will retry", warn: true });
});

// --- a save the server refused -----------------------------------------------------------------
//
// Out of wifi range is worth retrying for ever. "No such box" -- it was deleted
// for good on the other phone -- is not: the answer will be the same every
// thirty seconds until the tab is closed, and "will retry" would be a lie.

test("the page can say a failure is not worth retrying", async () => {
  const refusing = (failures, error) => (error.message === "offline" ? null : 2000);
  const { auto, timers, rec } = saver({ retryDelay: refusing });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");
  await settle();

  assert.equal(timers.pending(), 0, "a retry was scheduled anyway");
  assert.equal(auto.state("summary"), "failed");

  // Still there, still sendable by hand.
  rec.fail(false);
  auto.commit("summary");
  await settle();
  assert.equal(auto.state("summary"), "clean");
});

test("the default wait is there for the page to build its own rule on", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(retryAfter), [2000, 4000, 8000, 16000, 30000, 30000]);
});

test("a refusal is shown as what it is, with the reason, and no promise to retry", () => {
  const line = lineFor([{ state: "failed", policy: "pause", refused: "Box not found" }]);

  assert.deepEqual(line, { text: "Not saved — Box not found", warn: true });
});

// --- waiting for the saves that are out ------------------------------------------------------
//
// The page is about to be redrawn from the server, or Undo was pressed straight
// after typing. Either way what happens next must come *after* the save that
// is in flight: a redraw fetched a moment too early shows the old text as if
// it were current, and an undo racing a save can reach the server first.

test("idle resolves once every save in flight has landed", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  rec.hold();
  auto.edit("summary", "pans", "change");
  await settle();

  let idle = false;
  const waited = auto.idle().then(() => { idle = true; });
  await settle();
  assert.equal(idle, false, "idle resolved with a save still out");

  rec.release();
  await waited;
  assert.equal(auto.state("summary"), "clean");
});

test("idle waits for the follow-up save too, when the field was typed in meanwhile", async () => {
  const { auto, rec } = saver();
  auto.track("summary", "pots");
  rec.hold();
  auto.edit("summary", "pans", "change");
  await settle();
  auto.edit("summary", "pans and lids", "change");   // queued behind the one in flight

  const waited = auto.idle();
  rec.release();
  await waited;

  assert.deepEqual(rec.calls, [["summary", "pans"], ["summary", "pans and lids"]]);
  assert.equal(auto.state("summary"), "clean");
});

test("idle does not wait for a failure to be retried", async () => {
  const { auto, rec } = saver({ retryDelay: () => 30000 });
  auto.track("summary", "pots");
  rec.fail();
  auto.edit("summary", "pans", "change");

  await auto.idle();

  assert.equal(auto.state("summary"), "failed");
});

test("idle with nothing out resolves at once", async () => {
  const { auto } = saver();
  auto.track("summary", "pots");
  await auto.idle();
});

// --- giving up an edit that never got there ---------------------------------------------------
//
// "Not saved yet", with Undo beside it. The newest thing to take back is then
// the unsaved edit itself, and taking it back needs no server: the server
// never had it. Undoing the *save beneath it* instead would go back two steps.

test("an edit that failed to save can be given up, back to what the server has", async () => {
  const { auto, timers, rec } = saver({ retryDelay: () => 2000 });
  auto.track("summary", "pots");
  auto.edit("summary", "pans", "change");
  await settle();
  rec.fail();
  auto.edit("summary", "pans and lids", "change");
  await settle();
  const sent = rec.calls.length;

  assert.equal(auto.revert("summary"), "pans");

  assert.equal(auto.state("summary"), "clean");
  assert.equal(auto.value("summary"), "pans");
  timers.advance(60000);
  await settle();
  assert.equal(rec.calls.length, sent, "something was sent: a retry survived, or revert saved");
  // The save beneath it is untouched, and still undoable.
  rec.fail(false);
  assert.deepEqual(await auto.undo(), { key: "summary", value: "pots" });
});

test("reverting a field that is not tracked, or has nothing unsaved, is harmless", () => {
  const { auto } = saver();
  auto.track("summary", "pots");

  assert.equal(auto.revert("summary"), "pots");
  assert.equal(auto.revert("nonsense"), undefined);
});
