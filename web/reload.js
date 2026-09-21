// Keeping an open page up with deploys: an open page runs the old app.js until
// it is reloaded. These are the decisions -- which revision is running, which
// is live, whether a reload is wanted and safe, whether this tab already tried
// once -- with the wiring in app.js. Nothing here reads `document`, `location`
// or storage at import time, so it is tested under node.

// A backstop: a deploy drops the socket, whose reconnect checks at once, as
// does the page becoming visible. This catches a socket that never connects.
export const CHECK_MS = 60000;

// The revision this tab last reloaded itself towards: in sessionStorage, which
// is per tab and survives the reload.
export const TRIED_KEY = "moving.reloadedTowards";

const revision = (value) =>
  (typeof value === "string" && value && value !== "unknown" ? value : null);

// Read from a module's own URL, since every asset is served as
// `/app.js?v=<revision>`. A dev server serves no `?v=`: null, and auto-reload
// is off, or it would reload itself forever.
export function runningRevision(moduleUrl) {
  try {
    return revision(new URL(moduleUrl).searchParams.get("v"));
  } catch {
    return null;
  }
}

// null when /health could not be read. A failed check means "cannot tell",
// never "changed": the service is briefly down during a deploy.
export function liveRevision(health) {
  return revision(health && typeof health === "object" ? health.revision : null);
}

// `tried` is the revision this tab last reloaded towards: null for none,
// undefined when there is nowhere to remember it.
//
// - "off": a dev page; nothing to compare against.
// - "wait": the check failed; change nothing.
// - "current": nothing newer.
// - "reload": newer, and never tried -- reload at the next safe moment.
// - "offer": newer, but this tab already reloaded towards it and still is not
//   running it, or cannot remember trying. Reloading again could loop forever,
//   so the banner offers it and a person decides.
export function nextStep({ running, live, tried }) {
  if (!running) return "off";
  if (!live) return "wait";
  if (live === running) return "current";
  if (tried === undefined || tried === live) return "offer";
  return "reload";
}

// null for none, undefined when storage cannot be read at all.
export function readTried(storage) {
  try {
    return storage.getItem(TRIED_KEY);
  } catch {
    return undefined;
  }
}

// Remembered before the attempt. False if it could not be kept: then the reload
// must not happen by itself, since the new page could not tell it had tried.
export function rememberTried(storage, target) {
  try {
    storage.setItem(TRIED_KEY, target);
    return true;
  } catch {
    return false;
  }
}

// Whether a reload would destroy something right now. `held` is holdRefresh's
// answer. A reload also discards what a refresh leaves standing:
//
// - `dialog`: one is open (the add dialog may hold a photo just taken).
// - `busy`: a request of the person's is still out -- an upload, a print.
// - `owed`: a save has not landed; known only once saves are committed and
//   waited for, so the caller asks again then.
//
// `moment` "idle" also waits for `held`. "asked" (the banner was tapped) and
// "leaving" (the route is changing) do not: that touch is not one a reload
// would move.
export function reloadBlocked(state, moment = "idle") {
  const { held = false, dialog = false, busy = false, owed = false } = state || {};
  if (dialog || busy || owed) return true;
  return moment === "idle" && held;
}
