// Which photo stands for a box, and where its thumbnail comes from.
//
// The point of the feature is recognising a box by sight in a long list, so
// the two things worth pinning down are that a row always resolves to exactly
// one cover, and that a list never asks for a full-size image.
import assert from "node:assert/strict";
import { test } from "node:test";

import { analysisView, coverOf, coverUrl, flagIcon, kindIcon, stripFor, thumbUrl } from "../web/covers.js";

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

  assert.deepEqual(rowStatus({ kind: "tub", status: "packed" }), { kind: "tub", status: "packed", inside: "" });
  assert.deepEqual(rowStatus({ kind: "item", status: "loaded" }), { kind: "item", status: "loaded", inside: "" });
});

test("a record from before kinds existed is a box, and a new one is open", async () => {
  const { rowStatus } = await import("../web/covers.js");

  assert.deepEqual(rowStatus({}), { kind: "box", status: "open", inside: "" });
  assert.deepEqual(rowStatus({ kind: null, status: null }), { kind: "box", status: "open", inside: "" });
});

test("a container with a size says so, in front of what it is", async () => {
  // "The large box for the kitchen" is how people look for one.
  const { rowStatus } = await import("../web/covers.js");

  assert.equal(rowStatus({ kind: "box", size: "large", status: "open" }).kind, "large box");
  assert.equal(rowStatus({ kind: "tub", size: "small", status: "open" }).kind, "small tub");
  assert.equal(rowStatus({ kind: "bag", size: "medium", status: "packed" }).kind, "medium bag");
});

test("extra large is XL in the list: the cell is narrow and does not wrap", async () => {
  const { rowStatus } = await import("../web/covers.js");

  assert.equal(rowStatus({ kind: "crate", size: "extra large", status: "open" }).kind, "XL crate");
});

test("no size means no change: what it is, and nothing in front of it", async () => {
  const { rowStatus } = await import("../web/covers.js");

  for (const none of [null, undefined, ""]) {
    assert.deepEqual(rowStatus({ kind: "box", size: none, status: "packed" }), { kind: "box", status: "packed", inside: "" });
  }
  // The status line is untouched by any of this.
  assert.equal(rowStatus({ kind: "box", size: "large", status: "loaded" }).status, "loaded");
});

test("a container with things inside says how many, on a line of its own", async () => {
  // "3 inside" under the status: the count is about this row, not the ones
  // it lists, so it belongs on the row that has children.
  const { rowStatus } = await import("../web/covers.js");

  assert.equal(rowStatus({ kind: "crate", status: "open", child_count: 3 }).inside, "3 inside");
  assert.equal(rowStatus({ kind: "box", status: "open", child_count: 1 }).inside, "1 inside");
});

test("nothing inside means no line, not '0 inside'", async () => {
  const { rowStatus } = await import("../web/covers.js");

  for (const none of [0, null, undefined]) {
    assert.equal(rowStatus({ kind: "box", status: "open", child_count: none }).inside, "");
  }
  // The other two lines are untouched by the count.
  assert.deepEqual(rowStatus({ kind: "tub", size: "large", status: "packed", child_count: 2 }),
                   { kind: "large tub", status: "packed", inside: "2 inside" });
});

// --- a nested record's packing status is its container's -----------------------------
//
// "if its a subitem of a box, don't show the packing status, since we can
// assume they're closed." A bag inside a sealed crate has no packing state
// worth reading: it goes where the crate goes and is as closed as the crate
// is, so the cell was repeating the container's state, badly.

test("something inside a container does not carry a packing status", async () => {
  const { rowStatus } = await import("../web/covers.js");

  const said = rowStatus({ kind: "bag", status: "open", parent_code: "B-0001" });

  assert.equal(said.status, "");
  assert.equal(said.kind, "bag");
});

test("a top-level record still says how far along it is", async () => {
  const { rowStatus } = await import("../web/covers.js");

  for (const loose of [null, undefined, ""]) {
    assert.equal(rowStatus({ kind: "box", status: "packed", parent_code: loose }).status, "packed");
  }
});

test("the rule is the same function on both draw paths, in and back out again", async () => {
  // The bug this guards happened here once: the first draw showed one thing
  // and the live update overwrote it with another. A row put into a container
  // and taken out again must read the same way each time, whichever path drew.
  const { rowStatus } = await import("../web/covers.js");
  const bag = { kind: "bag", status: "packed", child_count: 2 };

  const loose = rowStatus({ ...bag, parent_code: null });
  const inside = rowStatus({ ...bag, parent_code: "B-0001" });
  const outAgain = rowStatus({ ...bag, parent_code: null });

  assert.equal(loose.status, "packed");
  assert.equal(inside.status, "");
  assert.deepEqual(outAgain, loose);
  // What it is, and what is in it, are the same either way.
  assert.equal(inside.kind, "bag");
  assert.equal(inside.inside, "2 inside");
});

test("where it is right now is not what this cell is for", async () => {
  // The cell used to show the location when there was one, which hid the
  // status. The location lives on the record page.
  const { rowStatus } = await import("../web/covers.js");

  const said = rowStatus({ kind: "box", status: "packed", current_location: "garage stack 3" });

  assert.deepEqual(said, { kind: "box", status: "packed", inside: "" });
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

// --- a quick read by default, a closer look when asked ---------------------------------

test("a closer look in progress says so, under the photo", async () => {
  const { analysisView } = await import("../web/covers.js");

  const running = analysisView({ status: "running", detail: true, remaining_ms: 9200, total_ms: 10000 }, 0);
  const queued = analysisView({ status: "pending", detail: true, remaining_ms: 19000, total_ms: 20000 }, 0);
  const overdue = analysisView({ status: "running", detail: true, remaining_ms: 1000, total_ms: 10000 }, 5000);

  assert.equal(running.label, "Looking closer… ~10 s");
  assert.match(queued.label, /^Queued… ~19 s$/);
  assert.equal(overdue.label, "Still looking…");
});

test("a quick read in progress is worded as it always was", async () => {
  const { analysisView } = await import("../web/covers.js");

  const running = analysisView({ status: "running", detail: false, remaining_ms: 6100, total_ms: 7000 }, 0);

  assert.equal(running.label, "Reading… ~7 s");
});

test("the viewer offers a closer look once a quick read is in", async () => {
  const { seenIn } = await import("../web/covers.js");

  const quick = seenIn({ status: "done", detail: false, items: [{ name: "kettle", qty: 1 }] });

  assert.equal(quick.closer, "offer");
});

test("it offers one even when the quick read found nothing -- that is when you want it", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn({ status: "done", detail: false, items: [] }).closer, "offer");
});

test("after a closer look it says so, and does not offer another", async () => {
  const { seenIn } = await import("../web/covers.js");

  const close = seenIn({ status: "done", detail: true, items: [{ name: "kettle", qty: 1 }] });

  assert.equal(close.closer, "done");
  assert.equal(close.heading, "Seen on a closer look");
});

test("while a closer look is running the viewer says that, not 'being read'", async () => {
  const { seenIn } = await import("../web/covers.js");

  const busy = seenIn({ status: "running", detail: true, items: null });

  assert.equal(busy.closer, null);
  assert.match(busy.note, /closer look/);
});

test("nothing is offered for a photo that was never read, is being read, or failed", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn(null).closer, null);
  assert.equal(seenIn({ status: "pending", detail: false }).closer, null);
  assert.equal(seenIn({ status: "error", detail: false, error: "offline" }).closer, null);
});

// --- (re)running the ordinary read, from the viewer -------------------------------------

test("a photo that was never read can be read from the viewer", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn(null).rerun, "Read this photo");
});

test("a photo that failed can be tried again", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn({ status: "error", error: "ollama is not running" }).rerun, "Try again");
});

test("a photo that has been read can be read again, quick or closer", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn({ status: "done", detail: false, items: [] }).rerun, "Read again");
  assert.equal(seenIn({ status: "done", detail: true, items: [] }).rerun, "Read again");
});

test("nothing is offered while a read is queued or running", async () => {
  // It would only queue a second job behind the first.
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn({ status: "pending" }).rerun, null);
  assert.equal(seenIn({ status: "running", detail: true }).rerun, null);
});

test("a photo of a single thing is never offered a read, and says why", async () => {
  // A bicycle has no contents to list, and the server refuses the request.
  const { seenIn } = await import("../web/covers.js");

  const seen = seenIn(null, { readable: false });

  assert.equal(seen.rerun, null);
  assert.equal(seen.closer, null);
  assert.match(seen.note, /single thing/);
});

// --- which mark stands for a record, and for a way of handling it ---------
//
// The empty thumbnail used to draw the same open box on every row, which said
// nothing. A <use> at a symbol that is not there draws nothing at all, in
// silence, so the mapping is worth pinning: an unknown kind must land on a
// mark that exists, not on a name built out of whatever the server said.

test("each kind of record has its own mark", () => {
  const marks = ["box", "tub", "crate", "bag", "item", "furniture"]
    .map((kind) => kindIcon({ kind }));
  assert.deepEqual(marks, ["i-box", "i-tub", "i-crate", "i-bag", "i-item", "i-furniture"]);
  assert.equal(new Set(marks).size, marks.length);
});

test("a record with no kind, or one this app has never heard of, still draws", () => {
  // rowStatus calls an absent kind a box; so does this, so the row's word and
  // its mark cannot disagree.
  assert.equal(kindIcon({}), "i-box");
  assert.equal(kindIcon({ kind: "" }), "i-box");
  assert.equal(kindIcon({ kind: "pallet" }), "i-box");
  assert.equal(kindIcon(null), "i-box");
});

test("the mark a row draws matches the word beside it", async () => {
  const { rowStatus } = await import("../web/covers.js");
  for (const kind of ["box", "tub", "crate", "bag", "item", "furniture"]) {
    assert.equal(kindIcon({ kind }), `i-${rowStatus({ kind }).kind}`);
  }
});

test("the handling flags carry the printed label's own glyphs", () => {
  // fragile and heavy are drawn on the tape too; that is the whole argument
  // for them being here. open_first is the one invented mark (a 1).
  assert.equal(flagIcon("fragile"), "i-fragile");
  assert.equal(flagIcon("heavy"), "i-heavy");
  assert.equal(flagIcon("open_first"), "i-open-first");
});

test("a flag nobody has drawn a mark for gets none, rather than a broken one", () => {
  assert.equal(flagIcon("wobbly"), "");
  assert.equal(flagIcon(undefined), "");
});

test("a read photo says which model read it", async () => {
  const { seenIn } = await import("../web/covers.js");

  // Two models can answer now -- the cloud tier, or the local one that stands
  // in when it cannot be reached -- so this panel, where a wrong item gets
  // traced, has to say which one did.
  const cloud = seenIn({ status: "done", summary: "tea things", items: [], model: "claude-sonnet-5" });
  const local = seenIn({ status: "done", summary: "tea things", items: [], model: "qwen3-vl:4b-instruct" });

  assert.equal(cloud.by, "Read by claude-sonnet-5");
  assert.equal(local.by, "Read by qwen3-vl:4b-instruct");
});

test("an older server that does not say which model is not a blank line", async () => {
  const { seenIn } = await import("../web/covers.js");

  assert.equal(seenIn({ status: "done", summary: "x", items: [] }).by, "");
  assert.equal(seenIn({ status: "running", items: null }).by, "");
});

test("money never reads as nothing while money has been spent", async () => {
  const { money } = await import("../web/covers.js");

  assert.equal(money(0), "$0.00");
  assert.equal(money(0.0075), "less than $0.01");
  assert.equal(money(0.75), "$0.75");
  assert.equal(money(30), "$30.00");
});

test("settings says which model is reading photos, and why when it is the local one", async () => {
  const { readingWith } = await import("../web/covers.js");

  const cloud = readingWith({
    provider: "claude", key: true, over: false, spent_usd: 0.75, cap_usd: 30,
    model: "claude-sonnet-5", detail_model: "claude-opus-5", local_model: "qwen3-vl:4b-instruct",
  });

  assert.equal(cloud.state, "cloud");
  assert.match(cloud.now, /claude-sonnet-5/);
  assert.match(cloud.why, /qwen3-vl:4b-instruct reads it instead/);
  assert.equal(cloud.spent, "$0.75");
  assert.equal(cloud.fraction, 0.025);
});

test("no key reads as local-only rather than as a fault", async () => {
  const { readingWith } = await import("../web/covers.js");

  const seen = readingWith({ provider: "claude", key: false, local_model: "qwen3-vl:4b-instruct" });

  assert.equal(seen.state, "nokey");
  assert.match(seen.now, /on this machine/);
  assert.match(seen.why, /ANTHROPIC_API_KEY in .env/);
});

test("past the cap the bar is full, not overflowing, and says how to carry on", async () => {
  const { readingWith } = await import("../web/covers.js");

  const seen = readingWith({
    provider: "claude", key: true, over: true, spent_usd: 42, cap_usd: 30,
    local_model: "qwen3-vl:4b-instruct",
  });

  assert.equal(seen.state, "over");
  assert.equal(seen.fraction, 1);
  assert.match(seen.why, /MOVING_VISION_BUDGET_USD above \$42\.00/);
});

test("a local-only setup is not described as out of budget", async () => {
  const { readingWith } = await import("../web/covers.js");

  const seen = readingWith({ provider: "ollama", local_model: "qwen3-vl:4b-instruct", cap_usd: 30 });

  assert.equal(seen.state, "local");
  assert.match(seen.why, /Nothing is spent/);
});

test("a key that is set but not working is said out loud, not left in a log", async () => {
  const { readingWith } = await import("../web/covers.js");

  const seen = readingWith({
    provider: "claude", key: true, over: false, spent_usd: 0, cap_usd: 30,
    model: "claude-sonnet-5", detail_model: "claude-opus-5", local_model: "qwen3-vl:4b-instruct",
    recent_reads: 6, recent_local: 6,
  });

  assert.equal(seen.state, "failing");
  assert.match(seen.now, /read on this machine/);
  assert.match(seen.why, /refused/);
});

test("one local read among several cloud ones is the fallback working, not a fault", async () => {
  const { readingWith } = await import("../web/covers.js");

  const seen = readingWith({
    provider: "claude", key: true, over: false, spent_usd: 1, cap_usd: 30,
    model: "claude-sonnet-5", detail_model: "claude-opus-5", local_model: "qwen3-vl:4b-instruct",
    recent_reads: 6, recent_local: 1,
  });

  assert.equal(seen.state, "cloud");
});
