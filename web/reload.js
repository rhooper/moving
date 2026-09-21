// Keeping an open page up with deploys. "add auto-reloading when the version
// changes -- probably needs a periodic check."
//
// Every deploy restarts the service, but an open phone keeps running the last
// deploy's app.js in memory until somebody reloads it by hand -- and with a
// dozen deploys a day, the phone in the kitchen is usually behind. Cache-
// busting made a reload fetch the new code; nothing made the reload happen.
//
// These are the decisions, kept pure so they can be tested: which revision the
// page is running, what the server says is live, whether a reload is wanted,
// whether one is safe right now, and whether this page has already reloaded
// itself towards that revision once. The wiring is in app.js. Nothing here
// reads `document`, `location` or storage at import time, so it loads under
// node.

/** The periodic check, while the page is visible. A backstop, not the main
 *  signal: a deploy restarts the service, which drops the socket, and the
 *  reconnect checks at once -- as does the page becoming visible again, which
 *  is the moment a phone is picked up off a box. The timer is for what those
 *  miss (a socket that never connects through some proxy), so it can be lazy:
 *  a minute bounds how long anyone looks at a stale app, and /health reads no
 *  database, so a check costs next to nothing while the screen is on anyway.
 *  Never while hidden: a phone in a pocket does not spend battery asking. */
export const CHECK_MS = 60000;

/** Where the revision this tab last reloaded itself towards is kept. Per tab,
 *  and surviving the reload, which is exactly sessionStorage. */
export const TRIED_KEY = "moving.reloadedTowards";

const revision = (value) =>
  (typeof value === "string" && value && value !== "unknown" ? value : null);

/**
 * The revision this page is running, from a module's own URL: every asset is
 * served as `/app.js?v=<revision>` (see api/assets.py), so the page knows
 * which deploy it came from without asking. A dev server serves files as
 * written, with no `?v=` -- null, and auto-reload is off, or a dev server would
 * reload itself forever.
 */
export function runningRevision(moduleUrl) {
  try {
    return revision(new URL(moduleUrl).searchParams.get("v"));
  } catch {
    return null;
  }
}

/** What /health says is live, or null when it could not be read. A failed
 *  check means "cannot tell" and never "changed": during a deploy the service
 *  is briefly down. */
export function liveRevision(health) {
  return revision(health && typeof health === "object" ? health.revision : null);
}

/**
 * What to do, given the revision running, the one live, and the one this tab
 * last reloaded itself towards (`tried`: null for none, undefined when there
 * is nowhere to remember it).
 *
 * - "off": a dev page; nothing to compare against.
 * - "wait": the check failed; change nothing.
 * - "current": nothing newer.
 * - "reload": newer, and never tried -- reload at the next safe moment.
 * - "offer": newer, but this tab has already reloaded itself towards it once
 *   and is still not running it (a stale shell from the service worker, say).
 *   Reloading again would do the same thing again, forever; so the banner
 *   offers it and a person decides. Also the answer when an attempt cannot be
 *   remembered: a page that cannot tell its second reload from its first does
 *   not start one by itself.
 */
export function nextStep({ running, live, tried }) {
  if (!running) return "off";
  if (!live) return "wait";
  if (live === running) return "current";
  if (tried === undefined || tried === live) return "offer";
  return "reload";
}

/** The revision this tab last reloaded towards: null for none, undefined when
 *  storage cannot be read at all (a private window, blocked site data). */
export function readTried(storage) {
  try {
    return storage.getItem(TRIED_KEY);
  } catch {
    return undefined;
  }
}

/** Remember an attempt before making it. False if it could not be kept --
 *  and then the reload must not happen by itself, since the page it lands on
 *  could not tell it had already tried. */
export function rememberTried(storage, target) {
  try {
    storage.setItem(TRIED_KEY, target);
    return true;
  } catch {
    return false;
  }
}

/**
 * Whether a reload would destroy something right now.
 *
 * A reload is the most violent refresh there is: it discards the page. So it
 * waits for everything a refresh waits for -- `held` is holdRefresh's own
 * answer (a focused or dirty field, a finger on the glass, the settle after
 * one), passed in rather than decided twice -- and for three things a refresh
 * does not care about, because a refresh leaves them standing and a reload
 * does not:
 *
 * - `dialog`: one is open. The add-inside dialog may have the camera live and
 *   a photo taken; the viewer may be over a modal.
 * - `busy`: a request of the person's is still out -- an upload, a print.
 * - `owed`: a save has not landed. Known only after the saves were committed
 *   and waited for, so the caller asks again then.
 *
 * `moment` is how the reload came about. "idle" is the page noticing by
 * itself, and waits for `held`. "asked" (the banner was tapped) and "leaving"
 * (the route is changing, so the page is being discarded anyway) do not: the
 * touch that caused them is not a thumb that a reload would move.
 */
export function reloadBlocked(state, moment = "idle") {
  const { held = false, dialog = false, busy = false, owed = false } = state || {};
  if (dialog || busy || owed) return true;
  return moment === "idle" && held;
}
