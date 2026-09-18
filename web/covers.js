// Which photo stands for a box, and where its thumbnail comes from.
//
// Kept out of app.js so it can be tested directly -- app.js touches the DOM at
// import time and cannot be loaded in isolation. Same arrangement as text.js.
//
// The list and the box page reach the same answer by different routes: a list
// row is told the cover id by the server (so a screenful of boxes stays one
// request), while the box page already has the whole photo list and works it
// out from the flags. Both end up here so they cannot drift apart.

/**
 * The URL of a photo's thumbnail.
 *
 * Always the ~400px thumb, never `/full`. A hundred-box list rendering 2048px
 * originals would be tens of megabytes over a phone's connection to draw
 * pictures the size of a postage stamp.
 *
 * Returns "" when there is no photo: an `<img src="">` re-requests the page
 * itself in some browsers, and `/photos/null/thumb` is a guaranteed 404 on
 * every row that has no picture.
 */
export function thumbUrl(photoId) {
  if (photoId === null || photoId === undefined || photoId === "") return "";
  return `/photos/${encodeURIComponent(photoId)}/thumb`;
}

/** The cover thumbnail for a box list row, from the id the list carried. */
export function coverUrl(box) {
  return thumbUrl(box && box.cover_photo_id);
}

/**
 * The photo a box is recognised by, given its photos.
 *
 * The server keeps exactly one flagged (the schema enforces it), so the
 * fallback to the first photo is only this side refusing to draw a strip with
 * no cover marked at all if that ever slips.
 */
export function coverOf(photos) {
  const list = photos || [];
  return list.find((photo) => photo.is_primary) || list[0] || null;
}

/** The box page's photo strip: every photo, its thumbnail, and which is cover. */
export function stripFor(photos) {
  const cover = coverOf(photos);
  return (photos || []).map((photo) => ({
    ...photo,
    thumb: thumbUrl(photo.id),
    cover: cover !== null && photo.id === cover.id,
  }));
}
