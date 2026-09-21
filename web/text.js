// Dictation arrives as one run-on phrase: "kettle, toaster and three mugs" is
// three items.
export function splitItems(raw) {
  return String(raw ?? "")
    .split(/[\n,;]+/)
    .map((part) => part.trim().replace(/^(and|then)\s+/i, "").trim())
    .filter(Boolean)
    .slice(0, 50);
}
