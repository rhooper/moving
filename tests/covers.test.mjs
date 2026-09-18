// Which photo stands for a box, and where its thumbnail comes from.
//
// The point of the feature is recognising a box by sight in a long list, so
// the two things worth pinning down are that a row always resolves to exactly
// one cover, and that a list never asks for a full-size image.
import assert from "node:assert/strict";
import { test } from "node:test";

import { coverOf, coverUrl, stripFor, thumbUrl } from "../web/covers.js";

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
