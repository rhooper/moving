// Pure text helpers, kept out of app.js so they can be tested directly --
// app.js touches the DOM at import time and cannot be loaded in isolation.

/**
 * Split a typed or dictated list into individual item names.
 *
 * Dictation arrives as a run-on phrase punctuated however the speech engine
 * felt like it, so "kettle, toaster and three mugs" has to become three items
 * rather than one long one.
 */
export function splitItems(raw) {
  return String(raw ?? "")
    .split(/[\n,;]+/)
    .map((part) => part.trim().replace(/^(and|then)\s+/i, "").trim())
    .filter(Boolean)
    .slice(0, 50);
}
