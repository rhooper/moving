// Autosave with undo, in place of Save and Cancel buttons.
//
// The logic is DOM-free so it can be tested here: time is injected, and "save"
// is whatever async function the page hands in.

import assert from "node:assert/strict";
import { test } from "node:test";

import { Autosaver, policyFor } from "../web/autosave.js";

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
