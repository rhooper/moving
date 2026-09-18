// Which photo stands for a box, and where its thumbnail comes from.
//
// The point of the feature is recognising a box by sight in a long list, so
// the two things worth pinning down are that a row always resolves to exactly
// one cover, and that a list never asks for a full-size image.
import assert from "node:assert/strict";
import { test } from "node:test";

import { analysisView, coverOf, coverUrl, stripFor, thumbUrl } from "../web/covers.js";

test("a thumbnail url points at the thumb, never the full image", () => {
  // A list of 200 boxes pulling 2048px originals is the failure this guards.
  assert.equal(thumbUrl(7), "/photos/7/thumb");
  assert.doesNotMatch(thumbUrl(7), /\/full$/);
});

test("no photo means no url at all, not a broken request", () => {
  // An <img src=""> re-requests the page itself in some browsers, and
  // /photos/null/thumb is a guaranteed 404 on every row without a photo.
  assert.equal(thumbUrl(null), "");
  assert.equal(thumbUrl(undefined), "");
  assert.equal(thumbUrl(""), "");
});

test("an id is encoded on its way into the path", () => {
  assert.equal(thumbUrl("a b/c"), "/photos/a%20b%2Fc/thumb");
});

test("a box list row uses the cover the server sent with it", () => {
  // The id rides along on the list row precisely so this needs no request.
  assert.equal(coverUrl({ code: "B-0001", cover_photo_id: 12 }), "/photos/12/thumb");
  assert.equal(coverUrl({ code: "B-0002", cover_photo_id: null }), "");
  assert.equal(coverUrl({ code: "B-0003" }), "");
  assert.equal(coverUrl(null), "");
});

test("the cover is the photo marked as one", () => {
  const photos = [
    { id: 1, is_primary: 0 },
    { id: 2, is_primary: 1 },
  ];

  assert.equal(coverOf(photos).id, 2);
});

test("with nothing marked the first photo stands in", () => {
  // Server-side there is always exactly one cover; this is only the client
  // refusing to draw a photo strip with no cover at all if that ever slips.
  assert.equal(coverOf([{ id: 5, is_primary: 0 }, { id: 6, is_primary: 0 }]).id, 5);
});

test("no photos means no cover", () => {
  assert.equal(coverOf([]), null);
  assert.equal(coverOf(null), null);
});

test("the photo strip marks exactly one cover and thumbnails the rest", () => {
  const strip = stripFor([
    { id: 1, is_primary: 0 },
    { id: 2, is_primary: 1 },
    { id: 3, is_primary: 0 },
  ]);

  assert.deepEqual(strip.map((p) => p.cover), [false, true, false]);
  assert.deepEqual(strip.map((p) => p.thumb), [
    "/photos/1/thumb",
    "/photos/2/thumb",
    "/photos/3/thumb",
  ]);
});

test("the strip keeps the fields the caption and delete controls need", () => {
  const strip = stripFor([{ id: 1, is_primary: 1, caption: "camping stove", width: 400, height: 300 }]);

  assert.equal(strip[0].caption, "camping stove");
  assert.equal(strip[0].width, 400);
  assert.equal(strip[0].height, 300);
});

test("an empty strip is an empty list, not a crash", () => {
  assert.deepEqual(stripFor([]), []);
  assert.deepEqual(stripFor(null), []);
});

test("the strip carries each photo's analysis through untouched", () => {
  const analysis = { status: "running", remaining_ms: 9000, total_ms: 20000 };
  const strip = stripFor([{ id: 1, is_primary: 1, analysis }, { id: 2, is_primary: 0, analysis: null }]);

  assert.deepEqual(strip[0].analysis, analysis);
  assert.equal(strip[1].analysis, null);
});

// --- what a photo's analysis looks like, and when ---------------------------
//
// The server sends a snapshot: how long is left *as of the reply*. Everything
// after that is this side's clock, so the function takes the snapshot and how
// long ago it arrived and says what to draw. The case that matters most is the
// estimate running out before the job does: a ring that fills and then just
// sits there full is a lie about being finished.

const running = { status: "running", remaining_ms: 12000, total_ms: 20000, items_found: null, error: null };

test("a photo nobody has analysed shows no state at all", () => {
  // Photos taken before analysis existed, and photos of a loose item.
  for (const nothing of [null, undefined]) {
    const view = analysisView(nothing);
    assert.equal(view.state, "none");
    assert.equal(view.busy, false);
    assert.equal(view.retry, false);
    assert.equal(view.label, "");
  }
});

test("a running job starts the ring where the server says it is", () => {
  const view = analysisView(running, 0);

  assert.equal(view.state, "running");
  assert.equal(view.busy, true);
  assert.equal(view.indeterminate, false);
  assert.ok(Math.abs(view.fraction - 0.4) < 1e-9, `fraction ${view.fraction}`);
  assert.equal(view.label, "Reading… ~12 s");
});

test("time passing on this side moves the ring and the seconds", () => {
  const view = analysisView(running, 6000);

  assert.ok(Math.abs(view.fraction - 0.7) < 1e-9, `fraction ${view.fraction}`);
  assert.equal(view.label, "Reading… ~6 s");
});

test("the seconds round up, so it never says zero while still counting", () => {
  assert.equal(analysisView(running, 11600).label, "Reading… ~1 s");
});

test("a job waiting behind others says so, and its wait includes theirs", () => {
  const queued = { status: "pending", remaining_ms: 25000, total_ms: 25000 };
  const view = analysisView(queued, 0);

  assert.equal(view.state, "pending");
  assert.equal(view.busy, true);
  assert.equal(view.fraction, 0);
  assert.equal(view.label, "Queued… ~25 s");
});

test("past zero and still not done, the ring gives up counting rather than sit full", () => {
  for (const elapsed of [12000, 12001, 90000]) {
    const view = analysisView(running, elapsed);
    assert.equal(view.busy, true);
    assert.equal(view.indeterminate, true, `at ${elapsed} ms`);
    // No fraction at all: there must be nothing a caller could draw as full.
    assert.equal(view.fraction, null);
    assert.equal(view.label, "Still reading…");
  }
  const queued = { status: "pending", remaining_ms: 1000, total_ms: 1000 };
  assert.equal(analysisView(queued, 5000).label, "Still queued…");
});

test("the fraction never reaches one while the job is unfinished", () => {
  for (let elapsed = 0; elapsed < 12000; elapsed += 500) {
    const { fraction } = analysisView(running, elapsed);
    assert.ok(fraction >= 0 && fraction < 1, `fraction ${fraction} at ${elapsed} ms`);
  }
});

test("an estimate that makes no sense is indeterminate, not a division by zero", () => {
  // total_ms of 0, missing, or smaller than what is left: no honest fraction.
  assert.equal(analysisView({ status: "running", remaining_ms: 5000, total_ms: 0 }).indeterminate, true);
  assert.equal(analysisView({ status: "running", remaining_ms: 5000 }).indeterminate, true);
  const odd = analysisView({ status: "running", remaining_ms: 5000, total_ms: 2000 });
  assert.equal(odd.indeterminate, false);
  assert.equal(odd.fraction, 0);
  assert.equal(odd.label, "Reading… ~5 s");
});

test("a clock that ran backwards does not un-finish the ring past its start", () => {
  // performance.now() is monotonic, but a caller could pass anything.
  const view = analysisView(running, -5000);
  assert.ok(Math.abs(view.fraction - 0.4) < 1e-9);
});

test("a finished job says how many things it saw", () => {
  const done = (n) => analysisView({ status: "done", remaining_ms: 0, total_ms: 9000, items_found: n });

  assert.equal(done(3).label, "3 items found");
  assert.equal(done(1).label, "1 item found");
  assert.equal(done(0).label, "Nothing recognised");
  assert.equal(done(3).busy, false);
  assert.equal(done(3).retry, false);
  // Elapsed time means nothing once it is done.
  assert.equal(analysisView({ status: "done", items_found: 2 }, 60000).label, "2 items found");
});

test("a finished job with no count still reads as finished", () => {
  assert.equal(analysisView({ status: "done", items_found: null }).label, "Photo read");
});

test("a failed job shows why, and offers another go", () => {
  const view = analysisView({ status: "error", remaining_ms: 0, total_ms: 0, error: "model not loaded" });

  assert.equal(view.state, "error");
  assert.equal(view.busy, false);
  assert.equal(view.retry, true);
  assert.equal(view.label, "model not loaded");
});

test("a long error is cut short for the strip, and kept whole for the tooltip", () => {
  const error = "connection refused ".repeat(20).trim();
  const view = analysisView({ status: "error", error });

  assert.ok(view.label.length <= 60, `label is ${view.label.length} long`);
  assert.ok(view.label.endsWith("…"));
  assert.equal(view.title, error);
});

test("a failure with no message still says something", () => {
  assert.equal(analysisView({ status: "error", error: null }).label, "Could not read this photo");
  assert.equal(analysisView({ status: "error", error: "   " }).label, "Could not read this photo");
});

test("a status this side has never heard of draws nothing rather than crashing", () => {
  assert.equal(analysisView({ status: "paused" }).state, "none");
  assert.equal(analysisView({}).state, "none");
});

// --- what a list row says about a record, beside its summary ----------------------
//
// Two lines: what it is, and how far along it is. The first draw and the live
// update used to disagree about this cell; one function means they cannot.

test("a row says what the record is, over where it has got to", async () => {
  const { rowStatus } = await import("../web/covers.js");

  assert.deepEqual(rowStatus({ kind: "tub", status: "packed" }), { kind: "tub", status: "packed" });
  assert.deepEqual(rowStatus({ kind: "item", status: "loaded" }), { kind: "item", status: "loaded" });
});

test("a record from before kinds existed is a box, and a new one is open", async () => {
  const { rowStatus } = await import("../web/covers.js");

  assert.deepEqual(rowStatus({}), { kind: "box", status: "open" });
  assert.deepEqual(rowStatus({ kind: null, status: null }), { kind: "box", status: "open" });
});

test("where it is right now is not what this cell is for", async () => {
  // The cell used to show the location when there was one, which hid the
  // status. The location lives on the record page.
  const { rowStatus } = await import("../web/covers.js");

  const said = rowStatus({ kind: "box", status: "packed", current_location: "garage stack 3" });

  assert.deepEqual(said, { kind: "box", status: "packed" });
});

// --- the photo viewer: what the model saw in *this* photo ------------------------------

test("a read photo lists what was seen in it, with counts", async () => {
  const { seenIn } = await import("../web/covers.js");

  const seen = seenIn({
    status: "done",
    summary: "tea things",
    items: [{ name: "kettle", qty: 1 }, { name: "mug", qty: 3 }],
  });

  assert.equal(seen.state, "done");
  assert.equal(seen.heading, "Seen in this photo");
  assert.equal(seen.summary, "tea things");
  assert.deepEqual(seen.items, [{ name: "kettle", qty: 1 }, { name: "mug", qty: 3 }]);
  assert.equal(seen.note, "");
});

test("a read photo with nothing in it says so rather than showing an empty list", async () => {
  const { seenIn } = await import("../web/covers.js");

  const seen = seenIn({ status: "done", summary: null, items: [] });

  assert.deepEqual(seen.items, []);
  assert.equal(seen.note, "Nothing was recognised in this photo.");
});

test("a photo still being read says that, and lists nothing yet", async () => {
  const { seenIn } = await import("../web/covers.js");

  for (const status of ["pending", "running"]) {
    const seen = seenIn({ status, items: null, summary: null });
    assert.equal(seen.state, "busy");
    assert.deepEqual(seen.items, []);
    assert.match(seen.note, /being read/);
  }
});

test("a photo that could not be read says why", async () => {
  const { seenIn } = await import("../web/covers.js");

  const seen = seenIn({ status: "error", error: "ollama is not running", items: null });

  assert.equal(seen.state, "error");
  assert.match(seen.note, /ollama is not running/);
});

test("a photo that was never read says so", async () => {
  // Taken before photos read themselves, or on a record with no contents.
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn(null).state, "none");
  assert.match(seenIn(null).note, /not been read/);
  assert.deepEqual(seenIn(undefined).items, []);
});

test("an old server that sends no list does not break the viewer", async () => {
  const { seenIn } = await import("../web/covers.js");

  const seen = seenIn({ status: "done", items_found: 2 });

  assert.deepEqual(seen.items, []);
});
