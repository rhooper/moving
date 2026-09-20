// Moving boxes -- phone-first PWA. Hash routing so a scanned label can land on
// /#/b/CODE without needing server-side routes for every view.

import { Autosaver, lineFor, policyFor, retryAfter } from "/autosave.js";
import { analysisView, coverUrl, rowStatus, seenIn, stripFor } from "/covers.js";
import {
  LiveChannel,
  SETTLE_MS,
  affects,
  hasUnsavedEdits,
  holdRefresh,
  onlySummaryChanged,
  partOf,
  reconcile,
} from "/live.js";
import { splitItems } from "/text.js";
import { KeyBuffer, entered } from "/wedge.js";

const STATUSES = ["open", "packed", "loaded", "delivered", "unpacked"];
const app = document.getElementById("app");

// Who this tab is, for the lifetime of this page. It rides on every write as
// X-Client-Id and comes back on the resulting notification, so this tab can
// tell its own echo from somebody else's change. Uniqueness among the few
// devices in one house is all that is needed, so Math.random is enough.
const clientId = `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;

// --- api ------------------------------------------------------------------

const keyStore = {
  get() {
    try { return localStorage.getItem("moving.key"); } catch { return null; }
  },
  set(value) {
    try { localStorage.setItem("moving.key", value); } catch { /* private mode */ }
  },
};

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  // Only declare JSON for a string body. Setting it for FormData would
  // override the multipart content-type and strip the boundary the browser
  // generates, and the upload would arrive unparseable.
  if (typeof options.body === "string") headers["content-type"] = "application/json";
  const key = keyStore.get();
  if (key) headers["X-API-Key"] = key;
  headers["X-Client-Id"] = clientId;

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    const entered = prompt("This server needs an access key.");
    if (entered) {
      keyStore.set(entered);
      return request(url, options);
    }
    throw new Error("An access key is required to use this server.");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const said = (await response.json()).detail;
      // A validation failure's detail is a list of objects, which as a message
      // reads "[object Object]". Its `msg` fields are the sentences.
      if (typeof said === "string" && said) detail = said;
      else if (Array.isArray(said)) detail = said.map((d) => d.msg).filter(Boolean).join("; ") || detail;
    } catch { /* not json */ }
    // The status rides along: autosave retries a server it could not reach,
    // and does not pester one that understood and said no.
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return response.status === 204 ? null : response.json();
}

// Most endpoints live under /api. Photo files and /b/{code} do not, so those
// callers use request() directly rather than faking a relative path.
const api = (path, options) => request(`/api${path}`, options);

// --- helpers --------------------------------------------------------------

// Every value interpolated into markup goes through this. The rule is
// absolute: no template literal below inserts a raw value, including ids and
// counts, so there is no judgement call about which fields are "safe".
function escape(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const forDestination = (rooms) => rooms.filter((r) => r.kind !== "source");
const forSource = (rooms) => rooms.filter((r) => r.kind !== "destination");

function roomOptions(rooms, selected) {
  return rooms
    .map((r) => `<option value="${escape(r.id)}"${r.id === selected ? " selected" : ""}>${escape(r.name)}</option>`)
    .join("");
}

function flagsOf(box) {
  return [
    box.fragile && "Fragile",
    box.open_first && "Open first",
    box.heavy && "Heavy",
  ].filter(Boolean);
}

function show(markup) {
  // A countdown belongs to the page it was drawn on. Every view change and
  // every whole-page redraw comes through here, so this is the one place
  // that guarantees the ring timer never outlives its photo strip.
  stopRings();
  app.innerHTML = markup;
  markPristine(app);
}

// Remember what every field held the moment it was drawn. That is the only
// way a later refresh can tell "nobody has touched this" from "half typed",
// and it costs one walk of a small form.
function markPristine(scope) {
  for (const field of scope.querySelectorAll("input, textarea, select")) {
    field.dataset.initial = fieldValue(field);
  }
}

function fieldValue(field) {
  return field.type === "checkbox" ? String(field.checked) : field.value;
}

// For code that sets a field's value itself (dictation). A programmatic
// `.value =` fires no input event, and the input event is what autosave on the
// record page listens for -- without this the dictated text would sit in the
// field looking saved and never be sent.
const edited = (field) => field.dispatchEvent(new Event("input", { bubbles: true }));

// Any button that reaches the server goes through this. Without it a slow
// action looks identical to a dead button, which is exactly how a print job
// ends up submitted five times.
async function busy(button, label, work) {
  const original = button.textContent;
  const wasDisabled = button.disabled;
  button.disabled = true;
  button.classList.add("working");
  button.textContent = label;
  try {
    return await work();
  } finally {
    button.classList.remove("working");
    button.textContent = original;
    button.disabled = wasDisabled;
  }
}

// Matches the server's rule, so the override only appears when it is needed.
function hasContents(box, items) {
  return Boolean((box.content_summary || "").trim()) || (items || []).length > 0;
}

function printerLine(press) {
  if (!press) return "";
  if (!press.prints) {
    return `<p class="say warn">${escape(press.detail)}</p>`;
  }
  const mark = press.ready ? "Printer ready" : "Printer unavailable";
  const cls = press.ready ? "say" : "say warn";
  return `<p class="${cls}">${escape(mark)} - ${escape(press.detail)}</p>`;
}

// The printer's state sits in the bar, visible from every page: finding out it
// is off only once you have scrolled to Print is finding out too late.
async function refreshPrinterBadge() {
  const badge = document.getElementById("printer-badge");
  if (!badge) return;
  try {
    const press = await api("/printer");
    const wrong = !press.ready || !press.prints;
    badge.hidden = !wrong;
    if (wrong) {
      badge.textContent = press.prints ? "Printer offline" : "Preview only";
      badge.title = press.detail;
    }
  } catch {
    badge.hidden = true;  // the server is unreachable; that is its own problem
  }
}

// The Web Speech API is Chrome-only; on Firefox and Safari the keyboard's own
// microphone does the job, so the button simply stays hidden rather than
// sitting there dead.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

function wireDictation(scope) {
  for (const button of scope.querySelectorAll("button.dictate")) {
    if (!Recognition) continue;
    button.hidden = false;
    button.addEventListener("click", () => {
      const field = scope.querySelector(`[name="${button.dataset.target}"]`);
      if (!field) return;
      const recogniser = new Recognition();
      recogniser.lang = navigator.language || "en-GB";
      recogniser.interimResults = false;
      button.classList.add("working");
      button.textContent = "Listening…";
      recogniser.onresult = (event) => {
        const said = Array.from(event.results).map((r) => r[0].transcript).join(" ");
        field.value = field.value ? `${field.value.trim()}, ${said}` : said;
        edited(field);
      };
      recogniser.onerror = () => announce("Could not hear anything.", { warn: true });
      recogniser.onend = () => {
        button.classList.remove("working");
        button.textContent = "Dictate";
      };
      recogniser.start();
    });
  }
}

function announce(message, { warn = false } = {}) {
  const banner = document.getElementById("say");
  if (!banner) return;
  banner.className = warn ? "say warn" : "say";
  banner.textContent = message;
  banner.hidden = false;
}

// For a view that could not be drawn at all: there is no page to go back to,
// so the error *is* the page.
function showError(message) {
  show(`<div class="err"><strong>${escape(message)}</strong></div>
        <p><a href="#/">Back to items</a></p>`);
}

// For an action that failed on a page that is still good. A dialog you can
// dismiss, and the page -- scroll position, typed text, your place in a long
// box -- is left exactly as it was. Replacing the view here is how a failed
// print used to cost you your spot.
function failed(message, title = "That did not work") {
  let dialog = document.getElementById("oops");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "oops";
    dialog.innerHTML = `<h2></h2><p></p>
      <form method="dialog"><button class="btn" autofocus>Dismiss</button></form>`;
    document.body.append(dialog);
  }
  dialog.querySelector("h2").textContent = title;
  dialog.querySelector("p").textContent = message;
  if (!dialog.open) dialog.showModal();
}

// Ask before destroying or hiding something. Resolves true only on the action
// button: Escape, the backdrop's own close, and Cancel are all "no". Cancel
// holds the focus, so a stray Enter or a double tap lands on the safe answer.
// A native <dialog> rather than confirm(): confirm() cannot be styled, names
// its buttons "OK" and "Cancel" whatever is at stake, and some mobile browsers
// suppress it outright after the first one.
function confirmed({ title, message, action }) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "ask";
    dialog.innerHTML = `<h2></h2><p></p>
      <form method="dialog" class="row">
        <button class="btn quiet" value="no" autofocus>Cancel</button>
        <button class="btn danger" value="yes"></button>
      </form>`;
    dialog.querySelector("h2").textContent = title;
    dialog.querySelector("p").textContent = message;
    dialog.querySelector("[value=yes]").textContent = action;
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "yes");
      dialog.remove();
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

// --- a record saves itself as it is edited ---------------------------------
//
// The record page has no Save and no Cancel. Text saves after a pause in
// typing and on leaving the field, a picker saves when it changes, and every
// save can be stepped back with Undo, newest first. *When* to save, what is
// still unsaved and how to take a save back all live in autosave.js, where
// they are tested without a browser; this is the part that knows about fields
// and the server.
//
// The saver outlives the fields. The page is redrawn under an open editing
// session -- a handling chip, a print, a change of kind all redraw it -- and
// the undo history, a save still in flight and any text the server has not
// got yet all have to survive that. So there is one session per open record,
// kept here, and each draw of the page plugs itself into it (`session.page`).

// The fields that save themselves, by `name`, and what Undo calls each one.
const AUTOSAVED = new Map([
  ["content_summary", "summary"],
  ["kind", "kind"],
  ["destination_room_id", "destination room"],
  ["source_room_id", "packed from"],
  ["source_location", "where in that room"],
  ["current_location", "location"],
]);
const ROOM_FIELDS = new Set(["destination_room_id", "source_room_id"]);

// What the saver compares and sends: text without the space either side. The
// field itself is left alone while it has the focus -- the space after "pots
// and" is somebody about to type the next word.
const tidy = (field) => (field.tagName === "SELECT" ? field.value : field.value.trim());

// The status line each of the record's three forms carries. The text is a
// polite live region, so "Saved" is spoken without taking the focus; Undo sits
// beside it rather than inside it, so it is not read out as part of every
// announcement. Always drawn and never hidden -- its height is reserved (see
// .autosave in index.html), so a line appearing does not move the page under
// a thumb.
const AUTOSAVE_LINE = `
  <p class="autosave">
    <span class="autosave-state" role="status"></span>
    <button type="button" class="undo" hidden>Undo</button>
  </p>`;

let editing = null;

function editSession(code) {
  // Coming back to a record after leaving it starts afresh: an Undo offered
  // an hour later would put back a value somebody else may have changed
  // since. Unless something is still unsaved -- that must not be dropped.
  const resumable = editing && editing.code === code
    && !(editing.left && !editing.auto.unsaved());
  if (resumable) {
    editing.left = false;
    return editing;
  }
  // A different record. Whatever the old one still owes the server it goes on
  // owing: its saver keeps its own retries going with no page attached.
  leaveRecord();

  const session = {
    code,
    page: null,         // the draw currently on screen: { landed, heard }
    last: new Map(),    // form id -> "saved" | "undone", for the status lines
    refused: new Map(), // field name -> the server's reason for saying no
    undoAt: null,       // the form whose line carries the Undo button
    left: false,
  };
  session.auto = new Autosaver({
    save: (key, value) => saveField(session, key, value),
    onState: (key, state, error) => {
      if (state === "failed" && error?.status >= 400 && error.status < 500) {
        session.refused.set(key, error.message);
      } else {
        session.refused.delete(key);
      }
      session.page?.heard(key, state);
    },
    // Retry a server that could not be reached; do not pester one that
    // understood the request and refused it.
    retryDelay: (failures, error) =>
      (error?.status >= 400 && error.status < 500 ? null : retryAfter(failures)),
  });
  editing = session;
  return session;
}

// One field per save, through the endpoints that were always there. The
// location has its own because every change of it is written to the box's
// history -- which is also why it alone waits to be left before it saves.
async function saveField(session, key, value) {
  const path = `/boxes/${encodeURIComponent(session.code)}`;
  const sent = ROOM_FIELDS.has(key) ? (value ? Number(value) : null) : (value || null);
  // keepalive: a save begun as the page is hidden or closed is still sent.
  const fresh = key === "current_location"
    ? await api(`${path}/location`, {
        method: "POST", keepalive: true, body: JSON.stringify({ current_location: sent }) })
    : await api(path, {
        method: "PATCH", keepalive: true, body: JSON.stringify({ [key]: sent }) });
  session.page?.landed(key, value, fresh);
}

// Going somewhere else, or the screen going dark, inside the pause: save now.
// Up to 1.2 s of typing is otherwise sitting in a timer that may never fire.
function leaveRecord() {
  if (!editing) return;
  editing.auto.commitAll();
  editing.page = null;
  editing.left = true;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) editing?.auto.commitAll();
});
addEventListener("pagehide", () => editing?.auto.commitAll());
addEventListener("online", () => editing?.auto.retryFailed());

// --- looking at one photo ------------------------------------------------------
//
// Tapping a photo opens it large, with what the model saw *in that photo*
// beside it: the evidence for one picture, so a wrong item on the contents
// list can be traced to where it came from. One viewer at a time; `showing`
// lets the photo strip repaint it if the model finishes while it is open.
const showing = { id: null, render: null };

function viewPhoto(photo, { readable = true } = {}) {
  const dialog = document.createElement("dialog");
  dialog.className = "viewer";
  const full = `/photos/${encodeURIComponent(photo.id)}/full`;
  dialog.innerHTML = `
    <img src="${escape(full)}" alt="${escape(photo.caption || "Box contents")}">
    <div class="seen">
      <h2></h2>
      <p class="meta summary" hidden></p>
      <ul class="items"></ul>
      <p class="meta note" hidden></p>
      <p class="reads" hidden>
        <button type="button" class="btn quiet" data-rerun hidden></button>
        <button type="button" class="btn quiet" data-closer hidden>Look closer</button>
        <span class="meta closer-hint" hidden>Look closer is a slower, more careful read.
          Either one adds to what is listed; neither removes anything.</span>
      </p>
      <p class="meta"><a href="${escape(full)}" target="_blank" rel="noreferrer">Open the picture on its own</a></p>
      <form method="dialog"><button class="btn" autofocus>Close</button></form>
    </div>`;

  const render = (latest) => {
    const seen = seenIn(latest.analysis, { readable });
    dialog.dataset.state = seen.state;
    setText(dialog.querySelector("h2"), seen.heading);
    const summary = dialog.querySelector(".summary");
    setText(summary, seen.summary);
    summary.hidden = !seen.summary;
    const note = dialog.querySelector(".note");
    setText(note, seen.note);
    note.hidden = !seen.note;
    // Built with DOM calls and textContent: the names are the model's words.
    const list = dialog.querySelector(".items");
    list.replaceChildren(...seen.items.map((item) => {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = item.name;
      row.append(name);
      if (item.qty > 1) {
        const qty = document.createElement("span");
        qty.className = "qty";
        qty.textContent = `×${item.qty}`;
        row.append(qty);
      }
      return row;
    }));
    list.hidden = !seen.items.length;
    const rerun = dialog.querySelector("[data-rerun]");
    rerun.hidden = !seen.rerun;
    if (seen.rerun) setText(rerun, seen.rerun);
    const offerCloser = seen.closer === "offer";
    dialog.querySelector("[data-closer]").hidden = !offerCloser;
    dialog.querySelector(".closer-hint").hidden = !offerCloser;
    dialog.querySelector(".reads").hidden = !seen.rerun && !offerCloser;
  };
  render(photo);

  // The quick model reads every photo; this asks the careful one. Nothing to
  // redraw here: the queued job comes back as photos.changed, the strip
  // repaints, and it repaints this viewer with it (see `showing`).
  //
  // Both buttons queue a job and draw what comes back. `busy()` puts a button's
  // old words back when it finishes, so the render comes after it, not inside.
  const ask = (button, query, problem) => button.addEventListener("click", async () => {
    try {
      const queued = await busy(button, "Asking…", () =>
        request(`/photos/${encodeURIComponent(photo.id)}/analyse${query}`, { method: "POST" }));
      render(queued);
    } catch (error) { failed(error.message, problem); }
  });
  ask(dialog.querySelector("[data-rerun]"), "", "Could not read the photo");
  ask(dialog.querySelector("[data-closer]"), "?detail=true", "Could not look closer");

  showing.id = photo.id;
  showing.render = render;
  // The backdrop is the dialog element itself; anything inside it is not.
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => {
    if (showing.render === render) { showing.id = null; showing.render = null; }
    dialog.remove();
  });
  document.body.append(dialog);
  dialog.showModal();
}

// --- parts of a box page that update on their own -------------------------
//
// The contents list and the photo strip change while the page is open, and
// not only from another phone: a photo is read in the background after an
// upload, and what it finds lands here seconds later -- usually while somebody
// is typing into the summary a few centimetres up the same page. A whole-page
// redraw is held while a field is focused (see "live updates" below), so these
// two are drawn by `reconcile` instead, the same way the box list is: the first
// draw and every later update are one code path, a row that survives keeps its
// element (and with it its listeners, its focus, and an <img> that does not
// reload and flash), and nothing outside the part is touched.
//
// Listeners are attached where a row is created and nowhere else. There is no
// "rebind after re-render" step to forget, which is how buttons go dead.
//
// Rows are built with DOM calls and textContent, as in rowFor: nothing is
// interpolated into markup, so escape() has nothing to do. The photo figure is
// the exception -- it is markup, and every value in it goes through escape().

/**
 * The "What is in it" list of one box.
 *
 * `changed(items)` is told whenever the list is redrawn from the server, so
 * the page can keep the things that depend on it (the delete note, and what
 * the print warning counts as "contents") in step. `stale()` is called when a refresh comes back to
 * find its list gone from the page -- a redraw overtook it, possibly with
 * older data than this refresh was carrying.
 */
function itemsPart(list, { path, changed, stale }) {
  let latest = 0;

  function draw(items) {
    reconcile(list, items, { key: (item) => item.id, create: itemRow, update: fillItemRow });
  }

  async function refresh() {
    // Two refreshes can be in flight at once (an event, and the person's own
    // rename). Only the one that started last may paint: it asked last, so it
    // holds the newest answer, whichever order the replies arrive in.
    const mine = ++latest;
    const items = await api(`${path}/items`);
    if (mine !== latest) return;
    if (!list.isConnected) { stale(); return; }
    draw(items);
    changed(items);
  }

  function itemRow(item) {
    const row = document.createElement("li");
    // What reconcile finds this row by next time. Without it every update
    // would make a second copy of the list beneath the first.
    row.dataset.key = item.id;
    const what = document.createElement("span");
    what.className = "what";
    // A button, not a span with a click handler: it takes focus, and Enter and
    // Space press it, without any of that being reimplemented here.
    const name = document.createElement("button");
    name.type = "button";
    name.className = "name";
    const tag = document.createElement("span");
    tag.className = "ai";
    tag.textContent = "autogenerated";
    what.append(name, tag);
    const qty = document.createElement("span");
    qty.className = "qty";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.remove = "";
    remove.textContent = "Remove";
    row.append(what, qty, remove);

    name.addEventListener("click", () => rename(row));
    // Removing one item deliberately does not ask first: it is one tap to
    // re-add, and a modal per row would make tidying what a photo found
    // miserable.
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await api(`/items/${encodeURIComponent(row.dataset.key)}`, { method: "DELETE" });
        await refresh();
      } catch (error) {
        failed(error.message);
      } finally {
        remove.disabled = false;
      }
    });
    return row;
  }

  function fillItemRow(row, item) {
    // The row's own record of what it shows. The handlers above read these at
    // the moment of the tap, so they act on the item as it is now rather than
    // as it was when the row was first made.
    row.dataset.name = item.name;
    const name = row.querySelector(".name");
    setText(name, item.name);
    name.setAttribute("aria-label", `Rename ${item.name}`);
    row.querySelector(".ai").hidden = item.source !== "ai";
    const qty = row.querySelector(".qty");
    setText(qty, item.qty > 1 ? `×${item.qty}` : "");
    qty.hidden = !(item.qty > 1);
    row.querySelector("[data-remove]").setAttribute("aria-label", `Remove ${item.name}`);
  }

  // Tap a name to change it. The name gives way to a field holding the same
  // text; Enter or leaving the field saves, Escape puts the name back. The
  // field is *removed* when it closes rather than hidden: a leftover input
  // whose value differs from what was drawn would look like an unsaved edit
  // to the live-update hold, and the page would never refresh again.
  function rename(row) {
    if (row.classList.contains("renaming")) return;
    const name = row.querySelector(".name");
    const was = row.dataset.name;
    const field = document.createElement("input");
    field.className = "rename";
    field.value = was;
    field.dataset.initial = was;
    field.autocomplete = "off";
    field.enterKeyHint = "done";
    field.setAttribute("aria-label", `New name for ${was}`);
    row.classList.add("renaming");
    name.hidden = true;
    name.after(field);
    field.focus();
    field.select();

    // Closing takes the focus away, which fires blur, which would save again;
    // and so does the error dialog. One flag covers every way back in.
    let finished = false;
    const close = (refocus) => {
      finished = true;
      field.remove();
      name.hidden = false;
      row.classList.remove("renaming");
      if (refocus) name.focus();
      fieldClosed();
    };
    const save = async (refocus) => {
      if (finished) return;
      const value = field.value.trim();
      // Emptied, or left as it was: nothing to say to the server.
      if (!value || value === was) { close(refocus); return; }
      finished = true;
      field.disabled = true;
      try {
        await api(`/items/${encodeURIComponent(row.dataset.key)}`, {
          method: "PATCH", body: JSON.stringify({ name: value }) });
      } catch (error) {
        close(refocus);
        failed(error.message, "Not renamed");
        return;
      }
      // Shown at once rather than after the refetch, so the old name does not
      // flash back for the length of a request.
      setText(name, value);
      close(refocus);
      // The server has also made the item this person's own (source goes from
      // "ai" to "manual"), so the refetch is what clears "autogenerated".
      try { await refresh(); } catch (error) { failed(error.message); }
    };

    field.addEventListener("keydown", (event) => {
      // Enter while an IME is composing picks a candidate; it is not "done".
      if (event.isComposing) return;
      if (event.key === "Enter") { event.preventDefault(); save(true); }
      if (event.key === "Escape") { event.preventDefault(); close(true); }
    });
    field.addEventListener("blur", () => save(false));
  }

  return { draw, refresh };
}

// One timer for the one photo strip on screen. Module-level so that show()
// can stop it without knowing anything about photos.
let ringTimer = null;

function stopRings() {
  clearInterval(ringTimer);
  ringTimer = null;
}

/** How often a spinner that has outrun its estimate asks whether it is done. */
const OVERDUE_ASK_MS = 5000;

/**
 * The photo strip of one box, including what the vision model is doing with
 * each photo.
 *
 * `contents` is whether this kind of record holds contents at all (a bicycle
 * does not, and its photos are never read). `ask()` requests a refresh of the
 * strip through the usual gate; `finished()` is called when a job that was
 * running on the last draw is done on this one.
 */
function photosPart(strip, { path, contents, changed, stale, ask, finished }) {
  // Each figure's photo as last heard from the server: a figure is built once
  // and updated in place, so the viewer must not show what it was born with.
  const lastHeard = new WeakMap();
  let latest = 0;
  let asked = 0;
  // figure -> the snapshot it is counting down from, and when that arrived.
  // The server says how long is left as of its reply; from there on the
  // countdown is this side's clock.
  const clocks = new Map();

  function draw(photos) {
    const before = new Set(clocks.keys());
    clocks.clear();
    const now = performance.now();
    let settled = false;
    reconcile(strip, stripFor(photos), {
      key: (photo) => photo.id,
      create: figureFor,
      update(figure, photo) {
        lastHeard.set(figure, photo);
        if (showing.id === photo.id) showing.render(photo);
        figure.classList.toggle("is-cover", photo.cover);
        figure.querySelector(".mark").hidden = !photo.cover;
        figure.querySelector("[data-cover]").hidden = photo.cover;
        const view = analysisView(photo.analysis, 0);
        if (view.busy) clocks.set(figure, { analysis: photo.analysis, since: now });
        else if (before.has(figure)) settled = true;
        paint(figure, view);
      },
    });
    stopRings();
    if (clocks.size) {
      // Reduced motion: the text still has to count, so the timer still runs,
      // but once a second and with no easing (see .ring-arc in index.html),
      // so the ring steps rather than sweeps. It never spins either way.
      const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
      ringTimer = setInterval(tick, calm ? 1000 : 200);
    }
    // Normally `items.changed` and `box.updated` arrive alongside the event
    // that led here. If those were lost in a reconnect, this is the only sign
    // that the list and the summary have moved; a duplicate costs two GETs.
    if (settled) finished();
  }

  function tick() {
    if (!strip.isConnected) { stopRings(); return; }
    const now = performance.now();
    let overdue = false;
    for (const [figure, clock] of clocks) {
      const view = analysisView(clock.analysis, now - clock.since);
      overdue = overdue || view.indeterminate;
      paint(figure, view);
    }
    // The estimate has run out. The event that says "done" is probably moments
    // away, but if it was dropped the spinner would turn forever -- so while
    // one is turning, ask now and then. Bounded: it stops with the spinner.
    if (overdue && now - asked > OVERDUE_ASK_MS) {
      asked = now;
      ask();
    }
  }

  function paint(figure, view) {
    const block = figure.querySelector(".analysis");
    if (block.dataset.analysis !== view.state) block.dataset.analysis = view.state;
    const state = block.querySelector(".state");
    setText(state, view.label);
    if (state.title !== view.title) state.title = view.title;

    const ring = figure.querySelector(".ring");
    ring.hidden = !view.busy;
    ring.classList.toggle("spin", view.indeterminate);
    // Filled by how much of the estimate has gone. Once there is no honest
    // fraction the inline offset is dropped and the stylesheet's spinner arc
    // takes over -- it can never be left sitting full.
    ring.querySelector(".ring-arc").style.strokeDashoffset =
      view.fraction === null ? "" : String(100 * (1 - view.fraction));

    // The same button is "Retry" after a failure and the way in for a photo
    // that has never been read (one taken before photos were read at all).
    const again = block.querySelector("[data-analyse]");
    const offered = view.retry || (view.state === "none" && contents);
    again.hidden = !offered;
    // busy() owns the label while a request is out, and puts it back after.
    if (offered && !again.classList.contains("working")) {
      setText(again, view.retry ? "Retry" : "Read this photo");
    }
  }

  async function refresh() {
    const mine = ++latest;
    const photos = await api(`${path}/photos`);
    if (mine !== latest) return;
    if (!strip.isConnected) { stale(); return; }
    draw(photos);
    changed(photos);
  }

  function figureFor(photo) {
    const figure = document.createElement("figure");
    figure.dataset.key = photo.id;  // what reconcile finds it by next time
    lastHeard.set(figure, photo);
    const id = encodeURIComponent(photo.id);
    // Everything that can change later is in here from the start and toggled
    // with `hidden`, so an update never has to create a control -- or remember
    // to give it a listener.
    figure.innerHTML = `
      <div class="pic">
        <a href="/photos/${escape(id)}/full" target="_blank" rel="noreferrer">
          <img src="${escape(photo.thumb)}" alt="${escape(photo.caption || "Box contents")}"
               width="${escape(photo.width)}" height="${escape(photo.height)}" loading="lazy">
        </a>
        <span class="ring" hidden>
          <svg viewBox="0 0 36 36" aria-hidden="true">
            <circle class="ring-track" cx="18" cy="18" r="15"/>
            <circle class="ring-arc" cx="18" cy="18" r="15" pathLength="100"
                    transform="rotate(-90 18 18)"/>
          </svg>
        </span>
      </div>
      <div class="acts">
        <span class="mark" hidden>Cover</span>
        <button type="button" data-cover aria-label="Use this photo as the cover">Make cover</button>
        <button type="button" data-drop-photo aria-label="Delete this photo">Delete</button>
      </div>
      <div class="analysis" data-analysis="none">
        <span class="state"></span>
        <button type="button" data-analyse hidden>Retry</button>
      </div>`;

    // Still a real link, so a long press or a middle click opens the file as
    // before; a plain tap opens the viewer, with what was seen in the photo.
    figure.querySelector(".pic a").addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      viewPhoto(lastHeard.get(figure) || photo, { readable: contents });
    });

    // Not under /api: photo files and their controls sit at the root, so
    // these go through request() rather than api().
    //
    // Each action redraws the strip and nothing else. They used to redraw the
    // whole page, which threw away anything typed elsewhere on it.
    const press = (selector, work) => {
      const button = figure.querySelector(selector);
      button.addEventListener("click", async () => {
        try {
          if (await work(button) === false) return;  // asked, and the answer was no
          await refresh();
        } catch (error) { failed(error.message); }
      });
    };
    press("[data-cover]", (button) =>
      busy(button, "Saving…", () => request(`/photos/${id}/cover`, { method: "POST" })));
    press("[data-drop-photo]", async (button) => {
      const sure = await confirmed({
        title: "Delete this photo?",
        message: "The picture file is destroyed. Photos have no bin, so this cannot be undone.",
        action: "Delete photo",
      });
      if (!sure) return false;
      return busy(button, "Deleting…", () => request(`/photos/${id}`, { method: "DELETE" }));
    });
    press("[data-analyse]", (button) =>
      busy(button, "Queueing…", () => request(`/photos/${id}/analyse`, { method: "POST" })));
    return figure;
  }

  return { draw, refresh };
}

// --- printing a label that will not say much ---------------------------------
//
// A label with no contents, or no destination room, is tape spent on something
// that cannot be sorted by sight. Not forbidden -- sometimes that is the label
// you want -- but it asks first. Returns whether to go ahead.
async function confirmThinLabel(what, { contents, room }) {
  const missing = [];
  if (!contents) missing.push("nothing is written down for it");
  if (!room) missing.push("it has no destination room");
  if (!missing.length) return true;
  const reasons = missing.join(", and ");
  return confirmed({
    title: `Print ${what} anyway?`,
    message: `${reasons[0].toUpperCase()}${reasons.slice(1)}. `
      + "The label will have a number and a QR code, and little else to sort it by.",
    action: "Print anyway",
  });
}

// --- a barcode reader ---------------------------------------------------------
//
// A keyboard-wedge reader types what it scans and presses Return: the label's
// Code 128 is the box number, its QR is the box URL. `entered` (wedge.js) says
// what a piece of text points at; this decides whether to go there.
//
// A URL can only have come from a label, so it is opened without asking. A
// bare number is only *shaped* like a code -- so is "kettle" -- and is looked
// up first, or every one-word search would land on "no such box".
async function openEntered(text) {
  const target = entered(text);
  if (!target) return false;
  if (!target.scanned) {
    try {
      await api(`/boxes/${encodeURIComponent(target.code)}`);
    } catch {
      return false;  // no such box: let the caller treat it as a search
    }
  }
  location.hash = `#/b/${encodeURIComponent(target.code)}`;
  return true;
}

// The reader has no idea where the cursor is. With a field focused its keys go
// into that field (the search box handles that, above); with *nothing* focused
// they would go nowhere, so they are collected here instead.
const scanKeys = new KeyBuffer();
document.addEventListener("keydown", async (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  // A field takes its own keys, and a modal is a question being asked.
  const typing = event.target instanceof Element
    && event.target.closest("input, textarea, select, [contenteditable]");
  if (typing || document.querySelector("dialog[open]")) return;

  const now = performance.now();
  // Firefox opens quick find on "/" and "'" when nothing is focused, which
  // would swallow the rest of a scanned URL. Only while a scan is under way:
  // a lone "/" still does what the browser means it to.
  if (scanKeys.collecting(now) && (event.key === "/" || event.key === "'")) {
    event.preventDefault();
  }

  const scannedText = scanKeys.feed(event.key, now);
  if (scannedText === null) return;
  // A *button* may well have the focus -- whichever was tapped last -- and the
  // reader's Return would press it again. After "Print label", that is a scan
  // that spends tape. The Return belongs to the scan, so it stops here.
  event.preventDefault();
  if (await openEntered(scannedText)) return;
  // Read something, but it is not a box here: show the search for it rather
  // than doing nothing, so a mis-scan is visible.
  location.hash = `#/search/${encodeURIComponent(scannedText.trim())}`;
});

// --- views ----------------------------------------------------------------

const boxesPath = (query) =>
  query ? `/search?q=${encodeURIComponent(query)}` : "/boxes?limit=100";

const listHeading = (boxes, query) =>
  query ? `Matches for “${query}”` : `${boxes.length} item${boxes.length === 1 ? "" : "s"}`;

// A live refresh updates the rows in place (see reconcile in live.js) rather
// than rebuilding the list's markup, so a row stays the same element across a
// refresh and a thumb already on it still opens the box it was aimed at.
//
// These rows are built with DOM calls and textContent, so no value is ever
// interpolated into markup and escape() has nothing to do here.
//
// The thumbnail frame is drawn for every row, photo or not. It holds its size
// from the stylesheet rather than from the image, so a box with no picture
// leaves a gap the same shape as its neighbours' -- and a box that gains one
// later fills that gap instead of shoving every row below it down the screen.
function rowFor(box) {
  const row = document.createElement("li");
  row.dataset.key = box.code;
  const link = document.createElement("a");
  link.setAttribute("href", `#/b/${encodeURIComponent(box.code)}`);
  const frame = document.createElement("span");
  frame.className = "t";
  const thumb = document.createElement("img");
  thumb.alt = "";  // decorative: the code beside it already names the box
  thumb.loading = "lazy";
  thumb.hidden = true;
  frame.append(thumb);
  link.append(frame);
  for (const cls of ["c", "s", "w"]) {
    const span = document.createElement("span");
    span.className = cls;
    link.append(span);
  }
  // Two lines in the last cell: what it is, over how far along it is.
  for (const cls of ["k", "st"]) {
    const line = document.createElement("span");
    line.className = cls;
    link.lastElementChild.append(line);
  }
  row.append(link);
  return row;
}

function fillRow(row, box) {
  const [code, summary, where] = row.querySelectorAll("span.c, span.s, span.w");
  setText(code, box.code);
  setText(summary, box.content_summary || "Nothing written down yet");
  const said = rowStatus(box);
  setText(where.querySelector(".k"), said.kind);
  setText(where.querySelector(".st"), said.status);
  setThumb(row.querySelector("span.t img"), coverUrl(box));
}

// Removing the attribute rather than setting src="" -- an empty src makes the
// browser re-request the page itself.
function setThumb(image, url) {
  if (!image) return;
  if (!url) {
    if (image.hasAttribute("src")) image.removeAttribute("src");
    image.hidden = true;
    return;
  }
  if (image.getAttribute("src") !== url) image.setAttribute("src", url);
  image.hidden = false;
}

function setText(node, value) {
  if (node && node.textContent !== value) node.textContent = value;
}

const patchBoxList = (list, boxes) =>
  reconcile(list, boxes, { key: (box) => box.code, create: rowFor, update: fillRow });

// Which page the browser thinks it is on. A background refresh has to prove
// it is still the right one before it paints: tapping a row starts a
// navigation that finishes long before a refetch begun a moment earlier does,
// and without this the list would land back on top of the box you just opened.
const here = () => location.hash || "#/";

async function refreshBoxes(query, at) {
  const boxes = await api(boxesPath(query));
  if (here() !== at) return;
  const list = document.getElementById("boxlist");
  if (!list || !boxes.length) {
    // Crossing into or out of the empty state changes the whole page, not
    // just the rows, so there is nothing to patch.
    await viewBoxes(query);
    return;
  }
  patchBoxList(list, boxes);
  setText(document.getElementById("list-heading"), listHeading(boxes, query));
}

async function viewBoxes(query) {
  const boxes = await api(boxesPath(query));

  // Must match rowFor/fillRow above element for element: a live refresh
  // patches these same rows in place rather than rebuilding them.
  const list = boxes.length
    ? `<ul class="boxlist" id="boxlist">${boxes.map((b) => `
        <li data-key="${escape(b.code)}"><a href="#/b/${escape(b.code)}">
          <span class="t">${coverUrl(b)
            ? `<img src="${escape(coverUrl(b))}" alt="" loading="lazy">`
            : '<img alt="" loading="lazy" hidden>'}</span>
          <span class="c">${escape(b.code)}</span>
          <span class="s">${escape(b.content_summary || "Nothing written down yet")}</span>
          <span class="w"><span class="k">${escape(rowStatus(b).kind)}</span><span
            class="st">${escape(rowStatus(b).status)}</span></span>
        </a></li>`).join("")}</ul>`
    : query
      ? `<div class="empty"><p>Nothing matches “${escape(query)}”.</p></div>`
      : `<div class="empty">
           <p>Nothing here yet.</p>
           <p><a href="#/new">Make the first one.</a></p>
         </div>`;

  show(`
    <form id="search" class="row" role="search">
      <input name="q" type="search" placeholder="Find an item, or something inside one"
             value="${escape(query || "")}" aria-label="Search items">
      <button class="btn" type="submit">Search</button>
    </form>
    <div class="section">
      <h2 id="list-heading">${escape(listHeading(boxes, query))}</h2>
      ${list}
    </div>`);

  document.getElementById("search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("q").trim();
    // A barcode reader types a box number (the Code 128) or a box URL (the
    // QR) and presses Return. Either opens the box; anything else searches.
    if (await openEntered(value)) return;
    location.hash = value ? `#/search/${encodeURIComponent(value)}` : "#/";
  });

  watch({ name: "list", query, refresh: (at) => refreshBoxes(query, at) });
}

// A whole-page draw fetches six things and paints when the slowest is back.
// An event that arrives in between may be applied to the page that is about
// to be replaced, and the replacement may have been fetched too early to
// include it -- the page would then be stale with nothing left to correct it.
// So whole-page draws are counted, and any part that changed while one was
// out is asked for again once the new page is up (see `overtaken` below).
async function viewBox(code, options) {
  // The fields are about to be replaced from the server. Anything sitting in
  // a pause is sent first, *and waited for*: a page fetched while the save was
  // still on its way would show the old text as if it were current, and then
  // adopt it as the baseline. What could not be saved at all is put back into
  // its field by the new page (see "carried over" in drawBox).
  if (editing?.code === code) {
    editing.auto.commitAll();
    await editing.auto.idle();
  }
  drawing += 1;
  try {
    await drawBox(code, options);
  } finally {
    drawing -= 1;
    if (!drawing) replayOvertaken();
  }
}

async function drawBox(code, { keepBanner = false, at = null } = {}) {
  const held = keepBanner ? document.getElementById("say")?.outerHTML : null;
  const path = `/boxes/${encodeURIComponent(code)}`;
  const [drawnBox, drawnItems, rooms, drawnPhotos, press, allKinds] = await Promise.all([
    api(path),
    api(`${path}/items`),
    api("/rooms"),
    api(`${path}/photos`),
    api("/printer").catch(() => null),
    api("/settings/kinds"),
  ]);
  // These three are kept up to date in place after the first draw, so they
  // are variables: the delete note and the print override read them later.
  let box = drawnBox;
  let items = drawnItems;
  let photos = drawnPhotos;
  const shape = allKinds.find((k) => k.kind === box.kind) || allKinds[0];
  // Five requests take a moment, and a thumb can navigate away inside it. A
  // background refresh says which page it was drawing for and gives up if
  // that is no longer the page. Foreground calls pass nothing and always win.
  if (at !== null && here() !== at) return;
  const room = rooms.find((r) => r.id === box.destination_room_id);
  const flags = flagsOf(box);

  show(`
    ${held || '<div id="say" class="say" hidden></div>'}
    ${box.deleted_at ? `
      <div class="say warn">
        <strong>Deleted.</strong> It is out of the list and out of search, and
        nothing has been destroyed.
        <div class="row" style="margin-top:0.5rem">
          <button class="btn" id="restore">Restore</button>
          <button class="btn quiet" id="purge">Delete permanently</button>
        </div>
      </div>` : ""}
    <h1 class="code">${escape(box.code)}</h1>
    ${flags.length ? `<div class="flags">${flags.map((f) => `<span class="flag">${escape(f)}</span>`).join("")}</div>` : ""}
    <div class="band" id="room-band"${room ? "" : " hidden"}>${escape(room?.name || "")}</div>
    <form id="summary-form">
      <label class="dlabel" for="what">${shape.contents ? "What is in it" : "What it is"}</label>
      <textarea id="what" name="content_summary" rows="2"
        placeholder="${shape.contents ? "pots, baking pans, stand mixer" : "Bicycle (Trek hybrid, blue)"}"
        >${escape(box.content_summary || "")}</textarea>
      ${AUTOSAVE_LINE}
      <div class="row">
        ${shape.contents ? '<button class="btn quiet" type="button" id="suggest">From contents</button>' : ""}
        <button class="btn quiet dictate" type="button" data-target="content_summary" hidden>Dictate</button>
      </div>
    </form>

    <div class="section">
      <h2>Where it is going</h2>
      <form id="destination">
        <label class="dlabel" for="kind">This is a</label>
        <select id="kind" name="kind">
          ${allKinds.map((k) => `<option value="${escape(k.kind)}"
            ${k.kind === box.kind ? "selected" : ""}>${escape(k.label)}</option>`).join("")}
        </select>
        <label class="dlabel" for="dest-room">Destination room</label>
        <select id="dest-room" name="destination_room_id">
          <option value="">Not decided yet</option>
          ${roomOptions(forDestination(rooms), box.destination_room_id)}
        </select>
        <label class="dlabel" for="src-room">Packed from</label>
        <select id="src-room" name="source_room_id">
          <option value="">Not recorded</option>
          ${roomOptions(forSource(rooms), box.source_room_id)}
        </select>
        <label class="dlabel" for="dest-from">Where in that room (optional)</label>
        <input id="dest-from" name="source_location" placeholder="shelf 3, under the desk"
               value="${escape(box.source_location || "")}">
        ${AUTOSAVE_LINE}
      </form>
    </div>

    <div class="section">
      <h2>Handling</h2>
      <div class="flags-set">
        ${[["fragile", "Fragile"], ["heavy", "Heavy"], ["open_first", "Open first"]]
          .map(([key, label]) => `
            <button class="chip ${box[key] ? "on" : ""}" data-flag="${escape(key)}"
              aria-pressed="${box[key] ? "true" : "false"}">${escape(label)}</button>`).join("")}
      </div>
      <p class="meta">These print on the label.</p>
    </div>

    <div class="section">
      <h2>Where it is now</h2>
      <div class="track" role="group" aria-label="Box status">
        ${STATUSES.map((s, i) => {
          const at = STATUSES.indexOf(box.status);
          const cls = i === at ? "now" : i < at ? "done" : "";
          return `<button class="step ${escape(cls)}" data-status="${escape(s)}"
                    ${i === at ? 'aria-current="true"' : ""}>${escape(s)}</button>`;
        }).join("")}
      </div>
      <form id="location" style="margin-top:0.75rem">
        <input name="current_location" placeholder="Truck, garage stack 3, storage…"
               value="${escape(box.current_location || "")}" aria-label="Current location"
               data-autosave="commit" enterkeyhint="done">
        ${AUTOSAVE_LINE}
      </form>
    </div>

    ${!shape.contents ? "" : `
    <div class="section">
      <h2>What is in it</h2>
      <p class="meta" id="items-hint" hidden>Tap a name to change it.</p>
      <ul class="items" id="items"></ul>
      <form id="add-item" style="margin-top:0.75rem">
        <textarea name="name" rows="2" required aria-label="Items"
          placeholder="kettle, toaster, three mugs"></textarea>
        <p class="meta">One per line, or separated by commas. Use your keyboard's
           microphone to dictate.</p>
        <div class="row">
          <button class="btn" type="submit">Add</button>
          <button class="btn quiet dictate" type="button" data-target="name" hidden>Dictate</button>
        </div>
      </form>
    </div>`}

    <div class="section">
      <h2>Photos</h2>
      <p class="meta">A photo of the open box before you tape it is the fastest
         record of what went in.${shape.contents ? ` Each one is read in the
         background, and what it shows is added to the list above.` : ""}</p>
      <p class="meta" id="cover-hint" hidden>The cover is the picture this box is
         shown by in the list.</p>
      <div class="shots" id="shots"></div>
      <div class="row" style="margin-top:0.75rem">
        <label class="btn" for="shot">Take a photo
          <input id="shot" type="file" accept="image/*" capture="environment" hidden>
        </label>
      </div>
      <p class="meta" id="upload-note" style="margin-top:0.5rem" hidden></p>
    </div>

    <div class="section">
      <h2>Label</h2>
      <p class="meta">Printed ${escape(box.label_print_count || 0)} time${box.label_print_count === 1 ? "" : "s"}.</p>
      ${printerLine(press)}
      <div class="row">
        <button class="btn quiet" id="print">Print label</button>
        <input id="copies" type="number" min="1" max="10" inputmode="numeric"
               value="${escape(press?.label_copies ?? 2)}" aria-label="Copies"
               style="flex:0 0 4.5rem;text-align:center">
      </div>
      <p class="meta">Copies. The usual number is set in Settings.</p>
    </div>

    ${box.deleted_at ? "" : `
    <div class="section danger">
      <h2>Delete</h2>
      <p class="meta" id="delete-note"></p>
      <div class="row">
        <button class="btn quiet" id="delete">Delete this ${escape(shape.label.toLowerCase())}</button>
      </div>
    </div>`}`);

  for (const button of app.querySelectorAll("[data-flag]")) {
    button.addEventListener("click", () => {
      const key = button.dataset.flag;
      act(() => api(path, { method: "PATCH", body: JSON.stringify({ [key]: !box[key] }) }));
    });
  }

  for (const button of app.querySelectorAll("[data-status]")) {
    button.addEventListener("click", () => act(() =>
      api(`/boxes/${encodeURIComponent(code)}/status`, { method: "POST", body: JSON.stringify({ status: button.dataset.status }) })));
  }

  // The two parts of the page that keep themselves up to date. Drawn here by
  // the same code that later updates them in place.
  const page = here();
  // A refresh that comes back to find its element gone was overtaken by a
  // redraw of this same page -- one that may have fetched before the change
  // this refresh was carrying. Ask the page now on screen for it again. If
  // the person has gone somewhere else, there is nothing to correct.
  const stale = (part) => () => { if (here() === page) requestPart(part); };

  const itemList = document.getElementById("items");
  const contents = itemList && itemsPart(itemList, {
    path,
    stale: stale("items"),
    changed(now) { items = now; recount(); },
  });
  contents?.draw(items);

  const strip = photosPart(document.getElementById("shots"), {
    path,
    contents: Boolean(shape.contents),
    stale: stale("photos"),
    ask: () => requestPart("photos"),
    finished() { requestPart("items"); requestPart("summary"); },
    changed(now) { photos = now; recount(); },
  });
  strip.draw(photos);

  // --- the three forms that save themselves ---
  //
  // What it is; where it is going and where it came from; where it is now.
  // They are still forms, so that Return in a single-line field means "save
  // this now" rather than reloading the page with the fields in the URL.
  const summaryForm = document.getElementById("summary-form");
  const destinationForm = document.getElementById("destination");
  const locationForm = document.getElementById("location");
  const summaryField = summaryForm.querySelector("[name=content_summary]");
  const savingForms = [summaryForm, destinationForm, locationForm];
  const fieldOf = (key) => app.querySelector(`[name="${key}"]`);
  const fieldsOf = (form) =>
    Array.from(form.querySelectorAll("input, textarea, select")).filter((f) => AUTOSAVED.has(f.name));

  const session = editSession(code);
  const auto = session.auto;

  // Carried over: a field the saver still owes the server -- a save in
  // flight, one that failed, a pause not yet run out when something redrew
  // the page -- gets its text back rather than the server's older value. Its
  // `dataset.initial` stays the server's, so it reads as unsaved to the
  // live-refresh hold, which is the truth. Everything else starts from here.
  for (const form of savingForms) {
    for (const field of fieldsOf(form)) {
      const owed = auto.state(field.name);
      if (owed && owed !== "clean") field.value = auto.value(field.name);
      else auto.track(field.name, tidy(field));
    }
  }

  // What Undo would take back if pressed now. An edit that is on its way to
  // being saved will be the newest save by the time an undo can run (pressing
  // Undo sends it first), so it is what the button names -- not the older
  // save underneath it.
  const owing = () => Array.from(AUTOSAVED.keys())
    .filter((key) => ["unsaved", "saving"].includes(auto.state(key)));
  // An edit that failed to save is newer still than anything that did save.
  const stuck = () => Array.from(AUTOSAVED.keys()).filter((key) => auto.state(key) === "failed");
  const undoTarget = () => owing()[0] || stuck()[0] || auto.canUndo();

  function drawLines() {
    const target = undoTarget();
    for (const form of savingForms) {
      const states = fieldsOf(form).map((field) => ({
        state: auto.state(field.name),
        policy: policyFor(field),
        refused: session.refused.get(field.name),
      }));
      const { text, warn } = lineFor(states, session.last.get(form.id));
      const line = form.querySelector(".autosave");
      setText(line.querySelector(".autosave-state"), text);
      line.classList.toggle("warn", warn);

      // One Undo on the page, on the line of the form last edited, and named
      // for what it will put back -- the history is one stack across all three
      // forms, so the next step may belong to a different one. It stays put
      // while a save is under way rather than blinking out and back on every
      // pause in typing, which also keeps it one Tab from the field.
      const undo = line.querySelector(".undo");
      undo.hidden = !(target && session.undoAt === form.id);
      if (!undo.hidden && !undo.classList.contains("working")) {
        setText(undo, `Undo ${target === "content_summary" && !shape.contents ? "name" : AUTOSAVED.get(target)}`);
      }
    }
  }

  function showRoom() {
    const now = rooms.find((r) => r.id === box.destination_room_id);
    const band = document.getElementById("room-band");
    setText(band, now?.name || "");
    band.hidden = !now;
  }

  // The kind decides which sections exist, so it is the one save that redraws
  // the page. Whatever had the focus gets it back, caret included: the redraw
  // is this page's doing, not the person's.
  async function redrawForKind() {
    const active = document.activeElement;
    const selector = active?.id ? `#${CSS.escape(active.id)}`
      : active?.name ? `[name="${CSS.escape(active.name)}"]` : null;
    let caret = null;
    try { caret = [active.selectionStart, active.selectionEnd]; } catch { /* not a text field */ }
    await viewBox(code, { keepBanner: true });
    const again = selector && app.querySelector(selector);
    if (!again || document.activeElement === again) return;
    again.focus();
    try { if (caret?.[0] != null) again.setSelectionRange(...caret); } catch { /* a picker */ }
  }

  session.page = {
    // A save reached the server. Nothing is redrawn -- the caret is in one of
    // these fields -- so the page's own idea of the record is brought up to
    // date by hand: `box` (the print button reads it), the room band, and the
    // field's `dataset.initial`, which is how the live-refresh hold knows the
    // field is no longer half-edited.
    landed(key, value, fresh) {
      box = { ...box, [key]: fresh[key], summary_source: fresh.summary_source, updated_at: fresh.updated_at };
      const field = fieldOf(key);
      // If the field still holds what was sent (give or take the spaces that
      // were not), it is pristine as it stands. If it has been typed in since,
      // what was sent is the baseline and the rest is still an edit.
      if (field) field.dataset.initial = tidy(field) === value ? field.value : value;
      if (key === "destination_room_id") showRoom();
      // A refresh held back behind this edit may be able to go now.
      fieldClosed();
    },
    heard(key, state) {
      const form = fieldOf(key)?.form;
      if (state === "saved" && form) {
        session.last.set(form.id, "saved");
        session.undoAt = form.id;
      }
      if (state === "saved" && key === "kind") { redrawForKind(); return; }
      drawLines();
    },
  };

  const onEdit = (event) => {
    const field = event.target;
    if (!AUTOSAVED.has(field.name)) return;
    auto.edit(field.name, tidy(field), policyFor(field));
    session.undoAt = field.form.id;
    drawLines();
  };
  const onLeave = (event) => {
    const field = event.target;
    if (!AUTOSAVED.has(field.name)) return;
    // Out of the field, so its stray spaces can go without moving a caret.
    if (field.tagName !== "SELECT" && field.value !== tidy(field)) {
      if (field.dataset.initial === field.value) field.dataset.initial = tidy(field);
      field.value = tidy(field);
    }
    auto.commit(field.name);
    drawLines();
  };
  // Return in a single-line field. For the location this is one of the two
  // ways it ever saves.
  const onReturn = (event) => {
    event.preventDefault();
    for (const field of fieldsOf(event.currentTarget)) auto.commit(field.name);
    drawLines();
  };
  summaryForm.addEventListener("submit", onReturn);
  destinationForm.addEventListener("submit", onReturn);
  locationForm.addEventListener("submit", onReturn);
  for (const form of savingForms) {
    // Both: a picker fires "change" everywhere but "input" only in newer
    // browsers, and the second of the two finds nothing new to save.
    form.addEventListener("input", onEdit);
    form.addEventListener("change", onEdit);
    form.addEventListener("focusout", onLeave);
    form.querySelector(".undo").addEventListener("click", (event) => undoLast(event.currentTarget));
  }

  // Step back the most recent save, whichever form it was in. A deliberate
  // press, not typing, so a failure here may say so in a dialog.
  //
  // Anything still on its way is sent first and waited for, so that Undo
  // pressed straight after typing takes back the typing. An undo racing the
  // save it is meant to follow could reach the server in either order.
  async function undoLast(button) {
    try {
      const step = await busy(button, "Undoing…", async () => {
        // One more try for anything that failed, too: if the network is back
        // it lands, and is then undone like any other save.
        auto.commitAll();
        await auto.idle();
        // Still not there. The newest thing to take back is then the unsaved
        // edit itself, and the server never had it: give it up here, and send
        // nothing. undo() would reach past it to the save beneath.
        const [lost] = stuck();
        if (lost) return { key: lost, value: auto.revert(lost) };
        return auto.undo();
      });
      if (!step) { drawLines(); return; }
      session.refused.delete(step.key);
      const field = fieldOf(step.key);
      if (field) {
        field.value = step.value;
        field.dataset.initial = step.value;
      }
      if (field?.form) session.last.set(field.form.id, "undone");
      if (step.key === "kind") await redrawForKind();
      else drawLines();
    } catch (error) { failed(error.message, "Not undone"); }
  }

  drawLines();
  wireDictation(app);

  // `box.updated`, applied in place when that is possible. It is what a
  // finished photo sends when it rebuilt the autogenerated summary -- and the
  // summary field is exactly where somebody may be typing at that moment.
  async function refreshSummary() {
    const fresh = await api(path);
    if (!summaryField.isConnected) { stale("summary")(); return; }
    const incoming = fresh.content_summary || "";
    const moved = incoming !== (summaryField.dataset.initial ?? "");
    const inUse = auto.state("content_summary") !== "clean" || hasUnsavedEdits([{
      value: summaryField.value,
      initial: summaryField.dataset.initial ?? "",
      focused: document.activeElement === summaryField,
    }]);
    // `box.updated` also covers the flags, the rooms and the kind. If any of
    // those moved, or the summary moved under somebody's cursor, this is a
    // job for the whole-page refresh and its rules: it redraws when it
    // safely can and raises the banner when it cannot. Never clobber typing.
    const needsPage = !onlySummaryChanged(box, fresh) || (moved && inUse);
    if (moved && !inUse) {
      // The value, the record of what was drawn, and the saver's baseline,
      // all three: the field must stay pristine. Miss the second and every
      // later refresh is held behind it; miss the third and the saver takes
      // the server's own text for an edit and saves it straight back -- as
      // this person's, which would stop photos ever updating it again.
      summaryField.value = incoming;
      summaryField.dataset.initial = incoming;
      auto.track("content_summary", tidy(summaryField));
      box = { ...box, content_summary: fresh.content_summary, summary_source: fresh.summary_source };
      recount();
    }
    if (needsPage) requestRefresh();
    else box = fresh;
  }
  // Absent for a loose thing: a bicycle has no contents to add to.
  const addForm = document.getElementById("add-item");
  addForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    // Dictation arrives as one run-on phrase, so split it rather than storing
    // "kettle, toaster and three mugs" as a single thing.
    const names = splitItems(new FormData(event.target).get("name"));
    if (!names.length) return;
    const button = addForm.querySelector("button[type=submit]");
    try {
      await busy(button, `Adding ${names.length}…`, async () => {
        for (const name of names) {
          await api(`${path}/items`, { method: "POST", body: JSON.stringify({ name }) });
        }
      });
      // Only once every name is in: a failure part-way leaves the text where
      // it is, to be fixed and sent again.
      addForm.reset();
    } catch (error) { failed(error.message); }
    // The list alone, and either way -- a failure part-way still added some.
    // Redrawing the whole page here used to discard anything half-typed in
    // the summary above.
    try { await contents.refresh(); } catch (error) { failed(error.message); }
  });
  const suggest = document.getElementById("suggest");
  suggest?.addEventListener("click", async () => {
    try {
      const { summary } = await busy(suggest, "Reading…", () =>
        api(`${path}/summary-suggestion`));
      const field = summaryField;
      if (!summary) {
        announce("Nothing to summarise yet - add some items first.", { warn: true });
        return;
      }
      // Fills the field and saves at once, like picking from a list: there is
      // no half-made choice to wait out, and Undo puts the old summary back.
      field.value = summary;
      auto.edit("content_summary", tidy(field), "change");
      session.undoAt = summaryForm.id;
      drawLines();
    } catch (error) { failed(error.message); }
  });

  // Spell out what is about to be destroyed. "Are you sure?" tells you
  // nothing; the count of photos and items, and whether a label for this code
  // is already stuck to something, are what actually inform the decision.
  //
  // Worked out when asked, not once at draw time: the items and photos change
  // under an open page now, and a confirmation that miscounts what it is
  // about to take is worse than one that does not count at all.
  const losses = () => [
    items.length && `${items.length} item${items.length === 1 ? "" : "s"}`,
    photos.length && `${photos.length} photo${photos.length === 1 ? "" : "s"}`,
  ].filter(Boolean);
  const printed = box.label_print_count || 0;

  // Everything outside the two parts that depends on what they hold. Called
  // after the first draw and again whenever either part changes.
  function recount() {
    const losing = losses();
    const note = document.getElementById("delete-note");
    if (note) {
      // Reversible, so the note describes what goes out of view rather than
      // warning about loss. Nothing here is destroyed.
      setText(note, [
        "Takes it out of the list and out of search. Nothing is destroyed, and you can restore it.",
        losing.length ? `Its ${losing.join(" and ")} go with it.` : "",
      ].filter(Boolean).join(" "));
    }
    document.getElementById("cover-hint").hidden = !photos.length;
    const hint = document.getElementById("items-hint");
    if (hint) hint.hidden = !items.length;
  }
  recount();

  const deleteButton = document.getElementById("delete");
  deleteButton?.addEventListener("click", async () => {
    const losing = losses();
    const sure = await confirmed({
      title: `Delete ${code}?`,
      message: [
        "It leaves the list and search on every device.",
        losing.length ? `Its ${losing.join(" and ")} go with it.` : "",
        "Nothing is destroyed: it goes to the bin, and you can restore it from there.",
      ].filter(Boolean).join(" "),
      action: "Delete",
    });
    if (!sure) return;
    try {
      await busy(deleteButton, "Deleting…", () => api(path, { method: "DELETE" }));
      location.hash = "#/";
    } catch (error) { failed(error.message); }
  });

  document.getElementById("restore")?.addEventListener("click", async (event) => {
    try {
      await busy(event.target, "Restoring…", () =>
        api(`${path}/restore`, { method: "POST" }));
      await viewBox(code);
    } catch (error) { failed(error.message); }
  });

  document.getElementById("purge")?.addEventListener("click", async (event) => {
    // The only irreversible action in the app, so this one does ask, and
    // spells out what a printed label will do afterwards.
    const losing = losses();
    const detail = [
      losing.length ? `Destroys its ${losing.join(" and ")}, including the photo files.` : "",
      printed ? `A label has been printed ${printed} time${printed === 1 ? "" : "s"} - if one is on something it will scan to nothing.` : "",
      `${code} will not be reused.`,
    ].filter(Boolean).join(" ");
    const sure = await confirmed({
      title: `Permanently delete ${code}?`,
      message: `${detail}\n\nThis cannot be undone.`,
      action: "Delete permanently",
    });
    if (!sure) return;
    try {
      await busy(event.target, "Deleting…", () =>
        api(`${path}/purge`, { method: "DELETE" }));
      location.hash = "#/";
    } catch (error) { failed(error.message); }
  });

  const printButton = document.getElementById("print");
  // The number of copies is a choice for this print, not part of the record:
  // nothing saves it, so it must never make the page look half-edited (which
  // would hold back live updates for good). Its baseline follows its value.
  const copiesField = document.getElementById("copies");
  copiesField.addEventListener("input", () => { copiesField.dataset.initial = copiesField.value; });

  printButton.addEventListener("click", async () => {
    const contents = hasContents(box, items);
    const sure = await confirmThinLabel(code, { contents, room: Boolean(box.destination_room_id) });
    if (!sure) return;

    const copies = Math.min(10, Math.max(1, Number(copiesField.value) || 1));
    let result;
    try {
      result = await busy(printButton, "Printing…", () =>
        api("/labels/print", {
          method: "POST",
          // allow_empty only ever follows a yes to the question above.
          body: JSON.stringify({ codes: [code], copies, allow_empty: !contents }),
        }));
    } catch (error) {
      failed(error.message, "Label not printed");
      return;
    }
    try {
      if (result.backend === "fake") {
        // The request succeeded and no tape came out. Saying "Printed" here
        // would be a lie, and it is the lie that gets the button pressed again.
        announce(
          `No label printed: the server is using the '${result.backend}' printer, ` +
          `which only writes a preview image. Set MOVING_PRINTER_BACKEND=brother_ql.`,
          { warn: true },
        );
      } else {
        const made = result.printed?.[0]?.copies ?? copies;
        announce(`Printed ${made} ${made === 1 ? "copy" : "copies"} of ${code}.`);
      }
      await viewBox(code, { keepBanner: true });
    } catch (error) { failed(error.message); }
  });

  document.getElementById("shot").addEventListener("change", async (event) => {
    const picker = event.target;
    const file = picker.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file, file.name || "photo.jpg");
    const note = document.getElementById("upload-note");
    note.textContent = `Uploading ${file.name || "photo"}…`;
    note.hidden = false;
    picker.disabled = true;
    try {
      // No content-type header: the browser must set the multipart boundary.
      await api(`${path}/photos`, { method: "POST", body });
      // The server has queued the photo for reading by the time it answers,
      // so the strip comes back with the countdown already on the new photo.
      await strip.refresh();
    } catch (error) { failed(error.message); }
    finally {
      note.hidden = true;
      picker.disabled = false;
      // Or choosing the same file again would not fire "change".
      picker.value = "";
    }
  });

  async function act(operation) {
    try { await operation(); await viewBox(code); }
    catch (error) { failed(error.message); }
  }

  // Last, so a half-built page is never the thing a refresh redraws.
  watch({
    name: "box",
    code,
    refresh: (at) => viewBox(code, { keepBanner: true, at }),
    // What can be brought up to date without redrawing the page; see partOf
    // in live.js for which event means which.
    parts: {
      items: () => contents?.refresh(),
      photos: () => strip.refresh(),
      summary: refreshSummary,
    },
  });
}

async function viewNew() {
  const [rooms, allKinds] = await Promise.all([api("/rooms"), api("/settings/kinds")]);
  show(`
    <h1 class="code">New</h1>
    <form id="new">
      <div class="section">
        <h2>What it is</h2>
        <select name="kind" aria-label="Kind">
          ${allKinds.map((k) => `<option value="${escape(k.kind)}">${escape(k.label)}</option>`).join("")}
        </select>
      </div>
      <div class="section">
        <h2>Where it is going</h2>
        <select name="destination_room_id" aria-label="Destination room">
          <option value="">Not decided yet</option>
          ${rooms.map((r) => `<option value="${escape(r.id)}">${escape(r.name)}</option>`).join("")}
        </select>
      </div>
      <div class="section">
        <h2>What is in it, or what it is</h2>
        <textarea name="content_summary" rows="3"
          placeholder="pots, baking pans, stand mixer &mdash; or Bicycle"></textarea>
        <select name="source_room_id" aria-label="Packed from" style="margin-top:0.5rem">
          <option value="">Packed from: not recorded</option>
          ${roomOptions(forSource(rooms))}
        </select>
        <input name="source_location" placeholder="Where in that room (optional)"
               style="margin-top:0.5rem">
        <label style="display:flex;gap:0.6rem;align-items:center;margin-top:0.75rem">
          <input type="checkbox" name="fragile" style="width:auto;min-height:auto"> Fragile
        </label>
      </div>
      <div class="section">
        <button class="btn" type="submit" id="create-stub" data-print="stub">Create and print stub</button>
        <p class="meta" style="margin:0.35rem 0 0">One inch of tape: just the number and the QR, to stick on before you pack.</p>
        <button class="btn quiet" type="submit" id="create" style="margin-top:0.75rem">Create</button>
        <button class="btn quiet" type="submit" id="create-print" data-print="label"
                style="margin-top:0.5rem">Create and print label</button>
      </div>
    </form>`);

  // The stub is the first button because it is the usual move: make the
  // record, stick an inch of tape on the empty box, pack, print the full
  // label at the end. The plain Create names what it makes, following the
  // kind picker.
  //
  // Enter, though, must never spend tape. A browser submits with the *first*
  // submit button when Enter is pressed in a field, which is now the one that
  // prints -- so Enter is pointed at the plain Create instead.
  document.getElementById("new").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
    event.preventDefault();
    event.currentTarget.requestSubmit(document.getElementById("create"));
  });
  const kindPicker = document.querySelector("#new [name=kind]");
  const nameCreate = () => {
    const chosen = allKinds.find((k) => k.kind === kindPicker.value);
    document.getElementById("create").textContent =
      `Create ${(chosen?.label || "box").toLowerCase()}`;
  };
  kindPicker.addEventListener("change", nameCreate);
  nameCreate();

  document.getElementById("new").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      kind: form.get("kind"),
      content_summary: form.get("content_summary").trim() || null,
      source_location: form.get("source_location").trim() || null,
      fragile: form.get("fragile") === "on",
    };
    const roomId = form.get("destination_room_id");
    if (roomId) payload.destination_room_id = Number(roomId);
    const sourceId = form.get("source_room_id");
    if (sourceId) payload.source_room_id = Number(sourceId);

    // Which button was pressed: data-print is "stub", "label", or absent.
    const pressed = event.submitter || document.getElementById("create");
    const printing = pressed.dataset.print || null;
    const wantsLabel = printing !== null;

    // Asked *before* creating: saying no should leave you on the form with
    // nothing made, not on a new record you did not mean to make yet. The
    // stub is exempt -- an empty box is what it is for.
    if (printing === "label") {
      const sure = await confirmThinLabel("its label", {
        contents: Boolean(payload.content_summary),
        room: Boolean(payload.destination_room_id),
      });
      if (!sure) return;
    }

    try {
      let unprinted = null;
      const box = await busy(pressed, wantsLabel ? "Creating and printing…" : "Creating…", async () => {
        const made = await api("/boxes", { method: "POST", body: JSON.stringify(payload) });
        if (wantsLabel) {
          try {
            await api("/labels/print", {
              method: "POST",
              // Anything thin about a full label was agreed to above.
              body: JSON.stringify({
                codes: [made.code], stub: printing === "stub", allow_empty: true,
              }),
            });
          } catch (error) { unprinted = error.message; }
        }
        return made;
      });
      location.hash = `#/b/${box.code}`;
      if (unprinted) {
        failed(`${box.code} was created, but its ${printing} did not print: ${unprinted}`,
               printing === "stub" ? "Stub not printed" : "Label not printed");
      }
    } catch (error) { failed(error.message, "Not created"); }
  });
}

// --- live updates ---------------------------------------------------------
//
// Two devices are packing the same house, so the screen has to follow the
// database rather than the last reload. The server only ever says *which box*
// changed; the refresh below refetches through the same endpoints the first
// draw used, so there is one code path deciding what a box looks like and a
// dropped or duplicated notification costs a wasted GET and nothing more.
//
// The hard part is not the socket. It is that a refresh is an interruption:
// it can take the keyboard away mid-word, throw away an unsaved edit, or --
// worst -- move a row out from under a thumb that is already coming down on
// it. So every refresh asks permission first, and when the answer is no it
// waits and says so instead.
//
// A box page has a second, gentler route. Its contents list, its photo strip
// and its summary can each be brought up to date on their own (`parts`), and
// that does not disturb a field somebody is typing in -- so those do not wait
// for the typing to stop, only for a finger to be off the glass. This is what
// lets a photo's findings appear in the list while the summary has the focus.

// The view currently on screen, and how to bring it up to date. Views that
// are not in this list -- the new-box form, the scanner -- are never
// refreshed at all: `affects` matches nothing for them.
let view = null;
let pending = false;
let flushTimer = null;

// Parts of the open box waiting for a still moment, and the timer watching
// for one.
const waiting = new Set();
let partTimer = null;

// Whole-page draws of a box in flight, and the parts that changed while one
// was. See viewBox: the new page may have been fetched too early to include
// them, so they are asked for again once it is up.
let drawing = 0;
const overtaken = new Set();

// Gesture state. `pointerDown` is the tap hazard proper; `lastTouch` keeps the
// screen still for a moment afterwards, because the click has not landed yet
// when the finger lifts.
let pointerDown = false;
let lastTouch = 0;

function watch(next) {
  view = { ...next, clientId, at: here() };
  // Whatever was just drawn is current by definition.
  pending = false;
  notice.hidden = true;
  forgetParts();
}

function holdState() {
  return {
    pointerDown,
    lastTouch,
    editing: hasUnsavedEdits(editableFields()),
  };
}

function editableFields() {
  return Array.from(app.querySelectorAll("input, textarea, select"))
    .filter((field) => field.type !== "file")
    .map((field) => ({
      value: fieldValue(field),
      initial: field.dataset.initial ?? "",
      focused: document.activeElement === field,
    }));
}

function requestRefresh() {
  if (!view || !view.refresh) return;
  // Nothing on a screen that is off needs to be right; `wake` catches up.
  if (document.hidden) { pending = true; return; }
  if (holdRefresh(holdState())) {
    pending = true;
    notice.hidden = false;
    scheduleFlush();
    return;
  }
  runRefresh();
}

async function runRefresh() {
  if (!view || !view.refresh) return;
  pending = false;
  notice.hidden = true;
  const target = view;
  try {
    await target.refresh(target.at);
  } catch {
    // A background refresh that fails must leave the screen alone. Replacing
    // a working page with an error because a poll lost the wifi for a second
    // would be worse than showing something a few seconds stale.
  }
}

// Bring one part of the open box up to date. Unlike a whole refresh this goes
// ahead while a field is focused or dirty -- it touches nothing a person can
// type into (the summary part checks that for itself) -- but it still waits
// out a gesture: a list that grows between pointerdown and click moves the
// thing being tapped, and one that grows mid-scroll makes the page jump.
function requestPart(part) {
  if (!view || !view.parts || !view.parts[part]) return;
  // Nothing on a screen that is off needs to be right; `wake` resyncs.
  if (document.hidden) { pending = true; return; }
  waiting.add(part);
  flushParts();
}

function flushParts() {
  clearTimeout(partTimer);
  partTimer = null;
  if (!waiting.size) return;
  if (!view || !view.parts) { waiting.clear(); return; }
  if (holdRefresh({ pointerDown, lastTouch })) {
    partTimer = setTimeout(flushParts, SETTLE_MS);
    return;
  }
  const { parts } = view;
  for (const part of waiting) {
    // As in runRefresh: a background update that fails leaves the screen
    // alone. A few seconds stale beats an error over a working page.
    Promise.resolve().then(() => parts[part]()).catch(() => {});
  }
  waiting.clear();
}

function forgetParts() {
  clearTimeout(partTimer);
  partTimer = null;
  waiting.clear();
}

function replayOvertaken() {
  const parts = Array.from(overtaken);
  overtaken.clear();
  for (const part of parts) requestPart(part);
}

// A field can stop being in the way by ceasing to exist: the inline rename
// box is removed from the page, and removing a focused element fires no
// focusout for the listener below to hear.
function fieldClosed() {
  if (pending) scheduleFlush();
}

function scheduleFlush() {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, SETTLE_MS);
}

function flush() {
  flushTimer = null;
  if (!pending || !view) return;
  const hold = holdState();
  // Still mid-edit: the notice stays up and the person decides. Waking this
  // again is the job of the focusout/change listeners below, not of a timer
  // that would otherwise tick forever behind a focused field.
  if (hold.editing) return;
  if (holdRefresh(hold)) { scheduleFlush(); return; }
  runRefresh();
}

const notice = document.getElementById("live");
notice.addEventListener("click", () => runRefresh());

addEventListener("pointerdown", () => {
  pointerDown = true;
  lastTouch = Date.now();
}, { capture: true, passive: true });

for (const kind of ["pointerup", "pointercancel"]) {
  addEventListener(kind, () => {
    pointerDown = false;
    lastTouch = Date.now();
    if (pending) scheduleFlush();
  }, { capture: true, passive: true });
}

// Scrolling is a gesture too: re-rendering under a moving list makes it jump.
addEventListener("scroll", () => { lastTouch = Date.now(); }, { capture: true, passive: true });

// Leaving a field, or committing a dropdown, is the moment a held refresh
// becomes safe again.
for (const kind of ["focusout", "change"]) {
  addEventListener(kind, () => { if (pending) scheduleFlush(); }, { capture: true, passive: true });
}

function socketUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const key = keyStore.get();
  // The key goes in the query because a browser's WebSocket constructor takes
  // a URL and nothing else -- there is no way to attach a header to the
  // handshake. Usually there is no key at all: this runs on a tailnet.
  return `${scheme}//${location.host}/api/events${key ? `?key=${encodeURIComponent(key)}` : ""}`;
}

const live = new LiveChannel({
  url: socketUrl,
  onEvent: (event) => {
    // Also drops this device's own echo: whoever made a change here has
    // already redrawn the part it touched.
    if (!affects(event, view)) return;
    // `view` can be null here: a resync affects everything, watched or not.
    const part = view?.parts ? partOf(event) : null;
    if (!part) { requestRefresh(); return; }
    if (drawing) overtaken.add(part);
    requestPart(part);
  },
});

async function viewSettings() {
  const [shape, press, rooms] = await Promise.all([
    api("/settings/code-format"),
    api("/printer").catch(() => null),
    api("/rooms"),
  ]);

  show(`
    <h1 class="code">Settings</h1>
    <div id="say" class="say" hidden></div>

    <div class="section">
      <h2>Box codes</h2>
      <p class="meta">Applies to boxes made from now on. Codes already printed
         onto tape never change.</p>
      <form id="code-format">
        <label class="dlabel" for="cf-prefix">Prefix</label>
        <input id="cf-prefix" name="prefix" value="${escape(shape.prefix)}"
               autocapitalize="characters" autocomplete="off">
        <label class="dlabel" for="cf-sep">Separator</label>
        <select id="cf-sep" name="separator">
          ${["-", "_", ".", ""].map((s) => `<option value="${escape(s)}"
            ${s === shape.separator ? "selected" : ""}>${s === "" ? "(none)" : escape(s)}</option>`).join("")}
        </select>
        <label class="dlabel" for="cf-digits">Length of the number</label>
        <input id="cf-digits" name="digits" type="number" min="1" max="12"
               value="${escape(shape.digits)}">
        <p class="meta" id="cf-example">Next: ${escape(shape.example)}</p>
        <div class="row">
          <button class="btn" type="submit">Save</button>
        </div>
      </form>
      <form id="seq" class="row" style="margin-top:0.75rem">
        <input name="number" type="number" min="1" placeholder="Next number, e.g. 1"
               aria-label="Next number">
        <button class="btn quiet" type="submit">Set</button>
      </form>
    </div>

    <div class="section">
      <h2>Printer</h2>
      ${printerLine(press)}
      <p class="meta">The printer is told not to power itself off each time
         this server starts, so it should stay awake on its own.</p>
      <form id="printing" class="row" style="margin-top:0.75rem">
        <label class="dlabel" for="label-copies" style="flex:1;align-self:center;margin:0">
          Copies of each label</label>
        <input id="label-copies" name="label_copies" type="number" min="1" max="10"
               inputmode="numeric" value="${escape(press?.label_copies ?? 2)}"
               style="flex:0 0 4.5rem;text-align:center">
        <button class="btn quiet" type="submit">Save</button>
      </form>
      <p class="meta">A box usually wants a label on more than one face. You can
         still change the number for a single print. A stub always prints one.</p>
    </div>

    <div class="section">
      <h2>Rooms</h2>
      <ul class="items">${rooms.map((r) => `
        <li><span>${escape(r.name)}</span><span class="qty">${escape(r.kind)}</span></li>`).join("")}</ul>
      <form id="new-room" style="margin-top:0.75rem">
        <div class="row">
          <input name="name" placeholder="Room name" aria-label="Room name" required>
          <select name="kind" aria-label="Kind" style="width:auto">
            <option value="destination">going to</option>
            <option value="source">packed from</option>
            <option value="both">both</option>
          </select>
          <button class="btn" type="submit">Add</button>
        </div>
      </form>
    </div>

    <div class="section">
      <h2>Deleted</h2>
      <p class="meta"><a href="#/deleted">Everything you have deleted</a> —
         restorable, and not destroyed until you say so.</p>
    </div>

    <div class="section">
      <h2>Your data</h2>
      <div class="row">
        <button class="btn quiet" id="do-backup">Back up now</button>
      </div>
      <p class="meta" style="margin-top:0.75rem">
        <a href="/api/export.json">Export JSON</a> &nbsp;
        <a href="/api/export.csv">Export CSV</a> &nbsp;
        <a href="/api/manifest.pdf">Manifest PDF</a>
      </p>
    </div>`);

  const codeForm = document.getElementById("code-format");
  const preview = () => {
    const form = new FormData(codeForm);
    const digits = Math.max(1, Math.min(12, Number(form.get("digits")) || 1));
    const n = "1".padStart(digits, "0");
    document.getElementById("cf-example").textContent =
      `Next: ${form.get("prefix")}${form.get("separator")}${n}`;
  };
  codeForm.addEventListener("input", preview);

  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const button = codeForm.querySelector("button[type=submit]");
    try {
      const saved = await busy(button, "Saving…", () =>
        api("/settings/code-format", {
          method: "PUT",
          body: JSON.stringify({
            prefix: form.get("prefix").trim(),
            separator: form.get("separator"),
            digits: Number(form.get("digits")),
          }),
        }));
      announce(`Codes will now look like ${saved.example}.`);
    } catch (error) { announce(error.message, { warn: true }); }
  });

  document.getElementById("printing").addEventListener("submit", async (event) => {
    event.preventDefault();
    const labelCopies = Number(new FormData(event.target).get("label_copies"));
    const button = event.target.querySelector("button");
    try {
      const saved = await busy(button, "Saving…", () =>
        api("/settings/printing", {
          method: "PUT", body: JSON.stringify({ label_copies: labelCopies }) }));
      const field = event.target.querySelector("[name=label_copies]");
      field.value = saved.label_copies;
      field.dataset.initial = String(saved.label_copies);
      announce(`Labels will print ${saved.label_copies} at a time.`);
    } catch (error) { announce(error.message, { warn: true }); }
  });

  document.getElementById("seq").addEventListener("submit", async (event) => {
    event.preventDefault();
    const number = Number(new FormData(event.target).get("number"));
    if (!number) return;
    const button = event.target.querySelector("button");
    try {
      const saved = await busy(button, "Setting…", () =>
        api("/settings/next-number", { method: "PUT", body: JSON.stringify({ number }) }));
      announce(`The next box will be ${saved.next}.`);
    } catch (error) { announce(error.message, { warn: true }); }
  });

  document.getElementById("new-room").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const button = event.target.querySelector("button[type=submit]");
    try {
      await busy(button, "Adding…", () =>
        api("/rooms", {
          method: "POST",
          body: JSON.stringify({ name: form.get("name").trim(), kind: form.get("kind") }),
        }));
      await viewSettings();
    } catch (error) { announce(error.message, { warn: true }); }
  });

  const backup = document.getElementById("do-backup");
  backup.addEventListener("click", async () => {
    try {
      const result = await busy(backup, "Backing up…", () =>
        api("/backup", { method: "POST" }));
      const mb = (result.bytes / 1048576).toFixed(1);
      announce(`Backed up ${mb} MB. ${result.kept} kept.`);
    } catch (error) { announce(error.message, { warn: true }); }
  });
}

async function viewDeleted() {
  const gone = await api("/boxes/deleted");

  show(`
    <h1 class="code">Deleted</h1>
    <p class="meta">Out of the list and out of search. Nothing here has been
       destroyed — open one to restore it.</p>
    ${gone.length ? `<ul class="boxlist">${gone.map((b) => `
      <li><a href="#/b/${escape(b.code)}">
        <span class="c">${escape(b.code)}</span>
        <span class="s">${escape(b.content_summary || "Nothing written down")}</span>
        <span class="w">${escape((b.deleted_at || "").slice(0, 10))}</span>
      </a></li>`).join("")}</ul>`
      : '<div class="empty"><p>Nothing deleted.</p></div>'}`);
}

// --- routing --------------------------------------------------------------

const routes = [
  [/^#?\/?$/, viewBoxes],
  [/^#\/search\/(.+)$/, (q) => viewBoxes(decodeURIComponent(q))],
  [/^#\/b\/([^/]+)$/, viewBox],
  [/^#\/new$/, viewNew],
  [/^#\/settings$/, viewSettings],
  [/^#\/deleted$/, viewDeleted],
  [/^#\/scan$/, () => import("/scan.js").then((m) => m.viewScan(show, showError))],
];

async function route() {
  const hash = location.hash || "#/";
  // Nothing is watched until a view says so. A view that throws, and every
  // view without a refresh of its own, therefore ends up unwatched rather
  // than inheriting the previous page's idea of what to redraw.
  view = null;
  pending = false;
  notice.hidden = true;
  forgetParts();
  // Leaving a record inside the pause autosave waits out: send it now. (If
  // this is the same record being opened again, the session is picked up.)
  leaveRecord();
  for (const [pattern, handler] of routes) {
    const match = hash.match(pattern);
    if (match) {
      for (const link of document.querySelectorAll(".bar a")) {
        link.toggleAttribute("aria-current", link.getAttribute("href") === hash);
      }
      try { return await handler(...match.slice(1)); }
      catch (error) { return showError(error.message); }
    }
  }
  showError(`No page at ${hash}`);
}

addEventListener("hashchange", route);
route();

refreshPrinterBadge();
setInterval(refreshPrinterBadge, 60000);
live.start();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* http, or blocked */ });
}
