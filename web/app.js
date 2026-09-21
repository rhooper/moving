// Hash routing, so a scanned label lands on /#/b/CODE with no server route per view.

import { Autosaver, lineFor, policyFor, retryAfter } from "/autosave.js";
import {
  analysisView, coverUrl, flagIcon, kindIcon, money, readingWith, rowStatus, seenIn, STRIP_SIZES,
  stripFor,
} from "/covers.js";
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
import {
  addedInside, addInsideRequest, blockedDelete, cameraTrouble, describe, editorSections, frameSize,
  groupMatches, inheritedRoom, kindsToAddInside, mayHold, notYetFragile, trail,
} from "/nesting.js";
import {
  CHECK_MS, liveRevision, nextStep, readTried, rememberTried, reloadBlocked, runningRevision,
} from "/reload.js";
import { choose, chosen, restrict, segmented } from "/segmented.js";
import { splitItems } from "/text.js";
import { KeyBuffer, entered } from "/wedge.js";

const STATUSES = ["open", "packed", "loaded", "delivered", "unpacked"];
const app = document.getElementById("app");

// Sent as X-Client-Id on every write and returned as the event's `origin`, so
// this tab can tell its own echo from another device's change.
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

// Writes in flight, which a reload waits out rather than abort. Autosaves are
// keepalive, survive the page going, and are waited for through idle() instead.
let writing = 0;

async function request(url, options = {}) {
  const counted = (options.method || "GET") !== "GET" && !options.keepalive;
  if (!counted) return send(url, options);
  writing += 1;
  try {
    return await send(url, options);
  } finally {
    writing -= 1;
    if (!writing) reloadIfAsked();
  }
}

async function send(url, options) {
  const headers = { ...options.headers };
  // Not for FormData: it would replace the multipart boundary the browser sets.
  if (typeof options.body === "string") headers["content-type"] = "application/json";
  const key = keyStore.get();
  if (key) headers["X-API-Key"] = key;
  headers["X-Client-Id"] = clientId;

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    const entered = prompt("This server needs an access key.");
    if (entered) {
      keyStore.set(entered);
      return send(url, options);
    }
    throw new Error("An access key is required to use this server.");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const said = (await response.json()).detail;
      // A 422's detail is a list of objects; their `msg` fields are the sentences.
      if (typeof said === "string" && said) detail = said;
      else if (Array.isArray(said)) detail = said.map((d) => d.msg).filter(Boolean).join("; ") || detail;
    } catch { /* not json */ }
    // Autosave reads the status: it retries an unreachable server, never a 4xx.
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return response.status === 204 ? null : response.json();
}

const api = (path, options) => request(`/api${path}`, options);

// --- helpers --------------------------------------------------------------

// Every value interpolated into markup goes through this, ids and counts included.
function escape(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// A mark from the sprite in index.html: as markup for template literals, as DOM
// for rows reconcile keeps. aria-hidden: the word beside it carries the meaning.
//
// createElementNS, because `document.createElement("svg")` makes an
// HTMLUnknownElement that silently renders nothing.
const SVG_NS = "http://www.w3.org/2000/svg";

const iconMarkup = (name, cls = "i") =>
  `<svg class="${escape(cls)}" aria-hidden="true"><use href="#${escape(name)}"/></svg>`;

function iconNode(name, cls = "i") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.append(document.createElementNS(SVG_NS, "use"));
  setIcon(svg, name);
  return svg;
}

function setIcon(svg, name) {
  const use = svg && svg.querySelector("use");
  if (use && use.getAttribute("href") !== `#${name}`) use.setAttribute("href", `#${name}`);
}

const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
const forDestination = (rooms) => rooms.filter((r) => r.kind !== "source").sort(byName);
const forSource = (rooms) => rooms.filter((r) => r.kind !== "destination").sort(byName);

const roomChoices = (rooms) => rooms.map((r) => ({ value: r.id, label: r.name }));
const kindChoices = (allKinds) => allKinds.map((k) => ({ value: k.kind, label: k.label }));
const sizeChoices = (sizes) => sizes.map((size) => ({ value: size, label: size[0].toUpperCase() + size.slice(1) }));

function mount(row) {
  const slot = app.querySelector(`[data-seg="${row.dataset.name}"]`);
  slot.replaceWith(row);
  return row;
}

function flagsOf(box) {
  return [
    box.fragile && { key: "fragile", label: "Fragile" },
    box.open_first && { key: "open_first", label: "Open first" },
    box.heavy && { key: "heavy", label: "Heavy" },
  ].filter(Boolean);
}

function show(markup) {
  // Every view change and whole-page redraw passes here, so no ring timer
  // outlives its photo strip.
  stopRings();
  app.innerHTML = markup;
  markPristine(app);
}

// What each field held when drawn: how a later refresh tells untouched from half typed.
function markPristine(scope) {
  for (const field of scope.querySelectorAll("input, textarea, select")) {
    field.dataset.initial = fieldValue(field);
  }
}

function fieldValue(field) {
  return field.type === "checkbox" || field.type === "radio" ? String(field.checked) : field.value;
}

// For code that sets `.value` itself: that fires no input event, and input is
// what autosave listens for, so the text would look saved and never be sent.
const edited = (field) => field.dispatchEvent(new Event("input", { bubbles: true }));

// Every button that reaches the server goes through this, so a slow action is
// not mistaken for a dead button and pressed again.
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

// Mirrors the server's print gate.
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
    badge.hidden = true;
  }
}

// Chrome only; elsewhere the Dictate button stays hidden.
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

// For a view that could not be drawn. An action that failed uses failed(),
// which leaves the page alone.
function showError(message) {
  show(`<div class="err"><strong>${escape(message)}</strong></div>
        <p><a href="#/">Back to items</a></p>`);
}

// One Escape fires `cancel` on every open modal dialog, not only the top one,
// and preventDefault() in the top one's handler does not stop it. So the key is
// taken in the capture phase and only the top dialog acts. Dialogs are appended
// to <body> just before they are shown, so document order is stacking order;
// and at keydown `[open]` is still accurate (by `cancel` the top one has
// already cleared its own).
function closesOnEscape(dialog, close) {
  const swallow = (event) => {
    if (event.key !== "Escape") return;
    const stack = document.querySelectorAll("dialog[open]");
    if (stack[stack.length - 1] !== dialog) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  };
  document.addEventListener("keydown", swallow, { capture: true });
  dialog.addEventListener("close", () =>
    document.removeEventListener("keydown", swallow, { capture: true }));
}

// For an action that failed on a page that is still good: a dialog, leaving the
// scroll position and typed text as they were.
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
  if (!dialog.open) {
    closesOnEscape(dialog, () => dialog.close());
    dialog.showModal();
  }
}

// Ask before destroying or hiding something. True only on the action button:
// Escape, the backdrop and Cancel are "no", and Cancel holds the focus so a
// stray Enter or double tap lands on the safe answer. Not confirm(), which some
// mobile browsers suppress after the first.
function confirmed({ title, message, action, dismiss = "Cancel" }) {
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
    dialog.querySelector("[value=no]").textContent = dismiss;
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "yes");
      dialog.remove();
    });
    document.body.append(dialog);
    closesOnEscape(dialog, () => dialog.close());
    dialog.showModal();
  });
}

// --- a record saves itself as it is edited ---------------------------------
//
// When to save, what is unsaved and Undo live in autosave.js; this part knows
// the fields and the server.
//
// One session per open record, keyed by code, because a sub-item's modal puts
// two records on screen at once. A session outlives the page's redraws (a chip,
// a print, a change of kind): its undo stack, a save in flight and unsent text
// must survive them. Each draw plugs itself in as `session.page`.

// The fields that save themselves, by `name`, and what Undo calls each one.
const AUTOSAVED = new Map([
  ["content_summary", "summary"],
  ["kind", "kind"],
  ["size", "size"],
  ["destination_room_id", "destination room"],
  ["source_room_id", "packed from"],
  ["source_location", "where in that room"],
  ["current_location", "location"],
  ["parent_code", "move"], // null takes it out of its container
]);
const ROOM_FIELDS = new Set(["destination_room_id", "source_room_id"]);

// Trimmed for comparing and sending only: a focused field keeps its trailing
// space, which is somebody about to type the next word.
const tidy = (field) => (field.tagName === "SELECT" ? field.value : field.value.trim());

// Undo sits outside the live region so it is not read with every announcement.
// Never hidden: its height is reserved (.autosave) so the page does not shift
// under a thumb.
const AUTOSAVE_LINE = `
  <p class="autosave">
    <span class="autosave-state" role="status"></span>
    <button type="button" class="undo" hidden>Undo</button>
  </p>`;

const sessions = new Map();   // code -> the session editing that record

function editSession(code) {
  // Returning to a record starts afresh -- a late Undo could put back a value
  // someone has changed since -- unless something is still unsaved.
  const open = sessions.get(code);
  if (open && !(open.left && !open.auto.unsaved())) {
    open.left = false;
    return open;
  }
  if (open) retire(open, { force: true });

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
      retire(session);
      if (state === "saved") reloadIfAsked();
    },
    retryDelay: (failures, error) =>
      (error?.status >= 400 && error.status < 500 ? null : retryAfter(failures)),
  });
  sessions.set(code, session);
  return session;
}

// A session is kept while a page is attached *or* it still owes the server: a
// failed save retries with no page in sight and must be able to land.
function retire(session, { force = false } = {}) {
  if (!force && (!session.left || session.auto.unsaved())) return;
  if (sessions.get(session.code) === session) sessions.delete(session.code);
}

// The location has its own endpoint because each change is written to the
// box's history, which is also why it alone saves only when left.
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

// Commit now: typing still inside its pause sits in a timer that may never fire.
function leaveRecord(session) {
  session.auto.commitAll();
  session.page = null;
  session.left = true;
  retire(session);
}

// Over a snapshot: retiring a session deletes it from the map underneath.
const everySession = (what) => { for (const session of Array.from(sessions.values())) what(session); };

// Navigating abandons the page and any modal over it.
const leaveEveryRecord = () => everySession(leaveRecord);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) everySession((session) => session.auto.commitAll());
});
addEventListener("pagehide", () => everySession((session) => session.auto.commitAll()));
addEventListener("online", () => everySession((session) => session.auto.retryFailed()));

// Wires forms to a record's session, for the record page and the sub-item
// modal alike. `root` scopes every lookup: `app`, or a dialog, whose fields are
// then outside the live-refresh hold (it walks `app`). `onKindSaved` redraws,
// since kind is the one save that changes which fields exist. `onReturn` is
// handed back rather than attached: a form with its own submit (the container
// look-up) must not also commit every field.
function autosaveFields({ root, session, forms, undoLabel, onLanded, onKindSaved }) {
  const auto = session.auto;
  const groupOf = (key) => root.querySelector(`.seg[data-name="${key}"]`);
  const fieldOf = (key) => root.querySelector(`[name="${key}"]`);
  const keysOf = (form) => Array.from(AUTOSAVED.keys()).filter((key) => fieldOf(key)?.form === form);
  const readKey = (key) => { const group = groupOf(key); return group ? chosen(group) : tidy(fieldOf(key)); };
  const showKey = (key, value) => {
    const group = groupOf(key);
    if (group) choose(group, value);
    else fieldOf(key).value = value;
  };
  // Makes `value` the baseline the live-refresh hold compares against. A row
  // pressed again since keeps that press as an edit still owed.
  const savedKey = (key, value) => {
    const group = groupOf(key);
    if (group) {
      const asShown = chosen(group) === String(value ?? "");
      for (const radio of group.querySelectorAll("input[type=radio]")) {
        radio.dataset.initial = String(asShown ? radio.checked : radio.value === String(value));
      }
      return;
    }
    const field = fieldOf(key);
    // Typed in since it was sent: what was sent is the baseline, the rest an edit.
    field.dataset.initial = tidy(field) === value ? field.value : value;
  };

  // A field still owed to the server (in flight, failed, or mid-pause when the
  // page redrew) gets its text back, not the server's older value. Its
  // `dataset.initial` stays the server's, so it still reads as unsaved.
  for (const form of forms) {
    for (const key of keysOf(form)) {
      const owed = auto.state(key);
      if (owed && owed !== "clean") showKey(key, auto.value(key));
      else auto.track(key, readKey(key));
    }
  }

  // What Undo would take back now. Undo sends a pending edit first, so that
  // edit is the one it names; a failed edit is newer than any that saved.
  const owing = () => Array.from(AUTOSAVED.keys())
    .filter((key) => ["unsaved", "saving"].includes(auto.state(key)));
  const stuck = () => Array.from(AUTOSAVED.keys()).filter((key) => auto.state(key) === "failed");
  const undoTarget = () => owing()[0] || stuck()[0] || auto.canUndo();

  function drawLines() {
    const target = undoTarget();
    for (const form of forms) {
      const states = keysOf(form).map((key) => ({
        state: auto.state(key),
        policy: policyFor(fieldOf(key)),
        refused: session.refused.get(key),
      }));
      const { text, warn } = lineFor(states, session.last.get(form.id));
      const line = form.querySelector(".autosave");
      setText(line.querySelector(".autosave-state"), text);
      line.classList.toggle("warn", warn);

      // One Undo, on the form last edited, named for what it puts back: the
      // history is one stack across the forms. It stays put while a save is
      // under way rather than blinking on every pause.
      const undo = line.querySelector(".undo");
      undo.hidden = !(target && session.undoAt === form.id);
      if (!undo.hidden && !undo.classList.contains("working")) {
        setText(undo, `Undo ${undoLabel(target)}`);
      }
    }
  }

  session.page = {
    // A save never redraws, which would take the caret out of a sentence. So
    // the page's copy is updated by hand: `box` (Print reads it), the room
    // band, and `dataset.initial`, which tells the live-refresh hold the field
    // is clean again.
    landed(key, value, fresh) {
      onLanded(key, value, fresh);
      if (fieldOf(key)) savedKey(key, value);
      // A refresh held back by this edit may go now.
      fieldClosed();
    },
    heard(key, state) {
      const form = fieldOf(key)?.form;
      if (state === "saved" && form) {
        session.last.set(form.id, "saved");
        session.undoAt = form.id;
      }
      if (state === "saved" && key === "kind") { onKindSaved(); return; }
      drawLines();
    },
  };

  const onEdit = (event) => {
    const field = event.target;
    if (!AUTOSAVED.has(field.name)) return;
    auto.edit(field.name, readKey(field.name), policyFor(field));
    session.undoAt = field.form.id;
    drawLines();
  };
  const onLeave = (event) => {
    const field = event.target;
    if (!AUTOSAVED.has(field.name)) return;
    // Trimmed only once left, when there is no caret to move.
    if (field.tagName !== "SELECT" && field.type !== "radio" && field.value !== tidy(field)) {
      if (field.dataset.initial === field.value) field.dataset.initial = tidy(field);
      field.value = tidy(field);
    }
    auto.commit(field.name);
    drawLines();
  };
  // Return in a single-line field; with leaving it, how the location saves.
  const onReturn = (event) => {
    event.preventDefault();
    for (const key of keysOf(event.currentTarget)) auto.commit(key);
    drawLines();
  };
  for (const form of forms) {
    // Both: older browsers fire only "change" for a picker; a second event
    // finds nothing new to save.
    form.addEventListener("input", onEdit);
    form.addEventListener("change", onEdit);
    form.addEventListener("focusout", onLeave);
    form.querySelector(".undo").addEventListener("click", (event) => undoLast(event.currentTarget));
  }

  // Anything pending is sent and waited for first, so Undo straight after
  // typing takes back the typing rather than racing its save to the server.
  // A deliberate press, so a failure may raise a dialog.
  async function undoLast(button) {
    try {
      const step = await busy(button, "Undoing…", async () => {
        // Retry failures too, but not a refusal: Undo beside "Not saved" means
        // give it up.
        for (const key of AUTOSAVED.keys()) {
          if (auto.state(key) !== "clean" && !session.refused.has(key)) auto.commit(key);
        }
        await auto.idle();
        // Still unsaved: drop the edit and send nothing. undo() would reach
        // past it to the save beneath.
        const [lost] = stuck();
        if (lost) return { key: lost, value: auto.revert(lost) };
        return auto.undo();
      });
      if (!step) { drawLines(); return; }
      session.refused.delete(step.key);
      const field = fieldOf(step.key);
      if (field) {
        showKey(step.key, step.value);
        savedKey(step.key, step.value);
      }
      if (field?.form) session.last.set(field.form.id, "undone");
      if (step.key === "kind") await onKindSaved();
      else drawLines();
    } catch (error) { failed(error.message, "Not undone"); }
  }

  return { drawLines, fieldOf, groupOf, keysOf, readKey, showKey, savedKey, onReturn };
}

// --- fragile climbs ------------------------------------------------------------
//
// Offers to mark fragile the containers of a fragile record that are not yet.
// Only on the page, and only if accepted: the server changes nothing itself.
// Clearing Fragile touches nothing else, and the containers' marks are outside
// the record's Undo. Resolves to the steps marked, so the caller can update its
// copy of `path` and not ask again.
async function offerFragileClimb(record) {
  const steps = notYetFragile(record.path);
  if (!steps.length) return [];
  const named = steps.map((step) => `${step.code} (${describe(step)})`);
  const list = named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named.at(-1)}` : named[0];
  const sure = await confirmed({
    title: "Mark what it is inside fragile too?",
    message: `${record.code} is inside ${list}, which ${steps.length === 1 ? "is" : "are"} not marked fragile. `
      + "Something fragile inside makes the whole thing fragile.",
    action: steps.length === 1 ? "Mark it fragile" : "Mark them fragile",
    dismiss: "Not now",
  });
  if (!sure) return [];
  for (const step of steps) {
    await api(`/boxes/${encodeURIComponent(step.code)}`, {
      method: "PATCH", body: JSON.stringify({ fragile: true }) });
    step.fragile = 1;
  }
  return steps;
}

// Upload size only: the server re-encodes.
const PHOTO_QUALITY = 0.82;

// --- adding something inside a container ----------------------------------------
//
// Resolves when the dialog closes. Creates the record, then uploads the photo.
// A failed upload leaves the record and keeps the dialog open to retry the
// photo, so a photo is never silently dropped.
function addInside({ parent, kinds, rooms, shape }) {
  const dialog = document.createElement("dialog");
  dialog.className = "ask adder";
  // Each paragraph on one line: a dialog's text keeps its line breaks.
  const what = escape(shape.label.toLowerCase());
  dialog.innerHTML = `
    <h2>Add something inside <span class="nb">${escape(parent.code)}</span></h2>
    <p class="meta">It goes where the ${what} goes. Everything else can be done from its own page.</p>
    <div data-seg="kind"></div>
    <p class="dlabel">A photo of it, if there is something to see</p>
    <div class="shotbox" id="adder-box" hidden>
      <video id="adder-cam" playsinline muted hidden></video>
      <img id="adder-still" alt="What was just photographed" hidden>
    </div>
    <div class="row" id="adder-shots" hidden>
      <button class="btn" type="button" id="adder-shutter">Take the photo</button>
      <button class="btn quiet" type="button" id="adder-retake" hidden>Take another</button>
    </div>
    <p class="meta" id="adder-photo">No photo yet, and none is needed. A photo is read in the background and names what is inside.</p>
    <div class="row">
      <label class="btn quiet" for="adder-shot">Choose a photo
        <input id="adder-shot" type="file" accept="image/*" hidden>
      </label>
    </div>
    <div data-seg="source_room_id"></div>
    <p class="meta warn" id="adder-said" hidden></p>
    <form method="dialog" class="row adder-acts">
      <button class="btn quiet" value="no" autofocus>Cancel</button>
      <button class="btn" value="add" id="adder-add">Add</button>
      <button class="btn quiet" value="open" id="adder-open">Add and open</button>
    </form>`;
  const slot = (name) => dialog.querySelector(`[data-seg="${name}"]`);
  const first = kindsToAddInside(kinds)[0];
  slot("kind").replaceWith(segmented({
    name: "kind", legend: "What it is", options: kindChoices(kindsToAddInside(kinds)), value: first?.kind }));
  slot("source_room_id").replaceWith(segmented({
    name: "source_room_id", legend: "Packed from", options: roomChoices(forSource(rooms)),
    optional: true, empty: "Not recorded" }));

  // A live viewfinder, started when the dialog opens rather than at page load.
  // Every camera failure (refused, none, a LAN address) is ordinary: the line
  // says which, the box is put away, and the file picker still works. The
  // picker has no `capture` on purpose: it is for a photo already taken.
  const shot = dialog.querySelector("#adder-shot");
  const photoLine = dialog.querySelector("#adder-photo");
  const box = dialog.querySelector("#adder-box");
  const cam = dialog.querySelector("#adder-cam");
  const still = dialog.querySelector("#adder-still");
  const shots = dialog.querySelector("#adder-shots");
  const shutter = dialog.querySelector("#adder-shutter");
  const retake = dialog.querySelector("#adder-retake");
  const say = (text) => setText(photoLine, text);
  let stream = null;
  let captured = null;   // { blob, name } from the viewfinder
  let preview = null;    // the object URL behind the thumbnail, to be revoked

  // The one place tracks stop: `close` fires for Cancel, Escape, the backdrop
  // and Add alike. A running track keeps the camera light on and drains the phone.
  const release = () => {
    if (stream) for (const track of stream.getTracks()) track.stop();
    stream = null;
    if (preview) { URL.revokeObjectURL(preview); preview = null; }
  };

  function showViewfinder() {
    box.hidden = false;
    still.hidden = true;
    cam.hidden = false;
    shots.hidden = false;
    shutter.hidden = false;
    retake.hidden = true;
    say("Point it at what is going in, then take the photo.");
  }

  function showPhoto(url, note) {
    box.hidden = false;
    still.src = url;
    still.hidden = false;
    cam.hidden = true;
    shots.hidden = !stream;
    shutter.hidden = true;
    retake.hidden = !stream;
    say(note);
  }

  async function startCamera() {
    // Checked first: on a plain LAN address getUserMedia fails with nothing
    // that explains itself.
    if (!window.isSecureContext) { say(cameraTrouble(null, { secure: false })); return; }
    say("Starting the camera…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
      });
    } catch (error) {
      say(cameraTrouble(error));
      return;
    }
    if (!dialog.isConnected) { release(); return; }   // closed while it was asking
    cam.srcObject = stream;
    await cam.play().catch(() => { /* autoplay refused; the frames still come */ });
    showViewfinder();
  }

  shutter.addEventListener("click", async () => {
    const size = frameSize(cam.videoWidth, cam.videoHeight);
    if (!size) { say("The camera has not quite started. Give it a moment."); return; }
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    canvas.getContext("2d").drawImage(cam, 0, 0, size.width, size.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", PHOTO_QUALITY));
    if (!blob) { say("That frame could not be kept. Take another, or choose a photo."); return; }
    if (preview) URL.revokeObjectURL(preview);
    preview = URL.createObjectURL(blob);
    captured = { blob, name: `${parent.code}-inside.jpg` };
    shot.value = "";   // this photo wins over an earlier choice
    showPhoto(preview, "This is the photo. It is read in the background and names what is inside.");
  });

  retake.addEventListener("click", () => {
    captured = null;
    showViewfinder();
  });

  shot.addEventListener("change", () => {
    const file = shot.files[0];
    if (!file) { say("No photo yet, and none is needed."); return; }
    captured = null;   // the chosen file wins over a frame taken here
    if (preview) URL.revokeObjectURL(preview);
    preview = URL.createObjectURL(file);
    showPhoto(preview, `Photo: ${file.name || "chosen"}. It will be read once the record exists.`);
  });

  const photoToSend = () => {
    if (captured) return captured;
    const file = shot.files[0];
    return file ? { blob: file, name: file.name || "photo.jpg" } : null;
  };

  // Once the record exists, Add retries the photo and Add and open opens it.
  let made = null;
  const said = dialog.querySelector("#adder-said");
  const acts = dialog.querySelector(".adder-acts");
  acts.addEventListener("submit", async (event) => {
    const pressed = event.submitter?.value || "no";
    if (pressed === "no") return;   // the form closes the dialog by itself
    event.preventDefault();
    const button = event.submitter;
    const source = chosen(dialog.querySelector('.seg[data-name="source_room_id"]'));
    const kind = chosen(dialog.querySelector('.seg[data-name="kind"]'));
    let photoError = null;
    try {
      await busy(button, made ? "Uploading…" : "Adding…", async () => {
        if (!made) {
          made = await api("/boxes", {
            method: "POST",
            body: JSON.stringify(addInsideRequest({ kind, parentCode: parent.code, sourceRoom: source })),
          });
        }
        const photo = photoToSend();
        if (photo) {
          const body = new FormData();
          body.append("file", photo.blob, photo.name);
          try {
            await api(`/boxes/${encodeURIComponent(made.code)}/photos`, { method: "POST", body });
          } catch (error) { photoError = error; }
        }
      });
    } catch (error) {
      setText(said, error.message);
      said.hidden = false;
      return;
    }
    // The socket drops this page's own write as an echo, so ask for the refetch.
    tell(addedInside(made, photoError), made.code);
    requestPart("summary");
    if (photoError) {
      setText(said, `${made.code} was added, but its photo did not upload: ${photoError.message}. `
        + "Try the photo again, add one from its page, or Cancel to leave it as it is.");
      said.hidden = false;
      setText(dialog.querySelector("#adder-add"), "Try the photo again");
      setText(dialog.querySelector("#adder-open"), "Open it");
      for (const radio of dialog.querySelectorAll("input[type=radio]")) radio.disabled = true;
      return;
    }
    dialog.close(pressed);
    if (pressed === "open") location.hash = `#/b/${made.code}`;
  });

  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close("no"); });
  // The tab going away is the one exit that does not fire `close`.
  addEventListener("pagehide", release);
  dialog.addEventListener("close", () => {
    release();
    removeEventListener("pagehide", release);
    dialog.remove();
  });
  document.body.append(dialog);
  closesOnEscape(dialog, () => dialog.close("no"));
  dialog.showModal();
  startCamera();
}

function tell({ text, warn }, code) {
  const line = document.getElementById("inside-said");
  if (!line) return;
  const link = document.createElement("a");
  link.setAttribute("href", `#/b/${encodeURIComponent(code)}`);
  link.textContent = "Open it";
  line.replaceChildren(text, " ", link);
  line.classList.toggle("warn", warn);
  line.hidden = false;
}

// --- editing something inside, without leaving the container --------------------
//
// A row in "Inside this crate" opens the child's editor over the container,
// with the child's own session and Undo; the container's page keeps its own.
// The child's page is still the whole record: this is a shortcut to it.
// Sections with nothing in them are folded (`editorSections`, native <details>).

let openEditor = null;

async function editSubitem(code, { kinds, rooms, onSaved }) {
  if (openEditor) return;
  const path = `/boxes/${encodeURIComponent(code)}`;
  let child;
  let items;
  let photos;
  try {
    [child, items, photos] = await Promise.all([
      api(path), api(`${path}/items`), api(`${path}/photos`)]);
  } catch (error) { failed(error.message, "Could not open it"); return; }

  const dialog = document.createElement("dialog");
  dialog.className = "editor";
  document.body.append(dialog);
  openEditor = dialog;

  const session = editSession(code);
  const auto = session.auto;
  const shapeOf = () => kinds.find((k) => k.kind === child.kind) || kinds[0];
  let contents = null;   // the items part, rebuilt with the body
  let strip = null;      // the photo strip, likewise
  let lines = () => {};  // the status line, until there is one

  // Called again only when the kind changes, which changes which sections
  // exist. The new form is wired afresh to the same session, which keeps the
  // undo stack and anything unsaved.
  function render() {
    const shape = shapeOf();
    const sections = editorSections(child, items, shape, photos);
    const fold = (section) => `
      <details class="fold" data-fold="${escape(section.key)}"${section.open ? " open" : ""}>
        <summary>${escape(section.legend)}</summary>
        <div class="fold-body" data-body="${escape(section.key)}"></div>
      </details>`;
    // Every section in one form, so the one status line covers them all. Adding
    // an item is therefore a button, not a form: forms cannot nest.
    dialog.innerHTML = `
      <h2>${escape(child.code)}</h2>
      <p class="meta" id="child-where"></p>
      <form id="child-edit">
        ${sections.map(fold).join("")}
        ${AUTOSAVE_LINE}
      </form>
      <p class="meta" id="child-links">
        <a href="#/b/${escape(encodeURIComponent(child.code))}" id="child-full">Open its whole page</a>
      </p>
      <div class="row">
        <button class="btn" type="button" id="child-close" autofocus>Close</button>
      </div>`;

    const body = (key) => dialog.querySelector(`[data-body="${key}"]`);
    // The legend still names the group for a screen reader; the <summary>
    // already shows the words.
    const quiet = (row) => { row.querySelector("legend").classList.add("vh"); return row; };

    const what = document.createElement("textarea");
    what.id = "child-what";
    what.name = "content_summary";
    what.rows = 2;
    what.value = child.content_summary || "";
    what.setAttribute("aria-label", sections.find((s) => s.key === "summary").legend);
    what.placeholder = shape.contents ? "pots, baking pans, stand mixer" : "Bicycle (Trek hybrid, blue)";
    body("summary").append(what);

    body("kind").append(quiet(segmented({
      name: "kind", legend: "Kind", options: kindChoices(kinds), value: child.kind })));
    if (body("size")) {
      body("size").append(quiet(segmented({
        name: "size", legend: "How big", options: sizeChoices(shape.sizes),
        value: child.size, optional: true, empty: "No size" })));
    }
    body("source").append(quiet(segmented({
      name: "source_room_id", legend: "Packed from", options: roomChoices(forSource(rooms)),
      value: child.source_room_id, optional: true, empty: "Not recorded" })));
    const where = document.createElement("input");
    where.id = "child-source-location";
    where.name = "source_location";
    where.value = child.source_location || "";
    where.placeholder = "shelf 3, under the desk";
    where.setAttribute("aria-label", "Where in that room");
    body("source").append(where);

    // Each flag PATCHes on its own, not through the saver.
    const flags = document.createElement("div");
    flags.className = "flags-set";
    for (const [key, label] of [["fragile", "Fragile"], ["heavy", "Heavy"], ["open_first", "Open first"]]) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.dataset.childFlag = key;
      chip.textContent = label;
      chip.addEventListener("click", () => toggleFlag(chip));
      flags.append(chip);
    }
    body("handling").append(flags);

    if (body("items")) {
      const list = document.createElement("ul");
      list.className = "items";
      list.id = "child-items";
      body("items").append(list);
      const add = document.createElement("div");
      add.className = "row";
      add.innerHTML = `<input id="child-add" aria-label="Add items" placeholder="kettle, toaster, three mugs">
        <button class="btn" type="button" id="child-add-go">Add</button>`;
      body("items").append(add);
      contents = itemsPart(list, {
        path,
        stale: () => {},   // the modal was closed or rebuilt under it
        changed(now) {
          items = now;
          unfoldFilled();
        },
      });
      contents.draw(items);
      const typed = document.getElementById("child-add");
      const addItems = async (button) => {
        const names = splitItems(typed.value);
        if (!names.length) return;
        try {
          await busy(button, `Adding ${names.length}…`, async () => {
            for (const name of names) {
              await api(`${path}/items`, { method: "POST", body: JSON.stringify({ name }) });
            }
          });
          typed.value = "";
        } catch (error) { failed(error.message); }
        try { await contents.refresh(); } catch (error) { failed(error.message); }
        onSaved();
      };
      const addButton = document.getElementById("child-add-go");
      addButton.addEventListener("click", () => addItems(addButton));
      // Return adds; the form's own submit would commit the fields instead.
      typed.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" || event.isComposing) return;
        event.preventDefault();
        addItems(addButton);
      });
    }

    // The record page's own strip; a thumbnail opens viewPhoto over this modal.
    if (body("photos")) {
      const shots = document.createElement("div");
      shots.className = "shots";
      body("photos").append(shots);
      strip = photosPart(shots, {
        path,
        contents: Boolean(shape.contents),
        stale: () => {},   // the modal was closed or rebuilt under it
        ask: () => strip?.refresh(),
        // A finished reading changed the items and the summary too.
        finished: () => { absorb(); },
        changed(now) {
          photos = now;
          onSaved();   // the container's row draws this record's cover
        },
      });
      strip.draw(photos);
    }

    const childForm = document.getElementById("child-edit");
    const wired = autosaveFields({
      root: dialog,
      session,
      forms: [childForm],
      undoLabel: (key) => (key === "content_summary" && !shape.contents ? "name" : AUTOSAVED.get(key)),
      onLanded(key, value, fresh) {
        child = { ...child, ...fresh };
        // Refetch the container's row now; this page's own echo is dropped.
        onSaved();
      },
      onKindSaved: redrawForKind,
    });
    lines = wired.drawLines;
    childForm.addEventListener("submit", wired.onReturn);
    document.getElementById("child-close").addEventListener("click", () => dismiss());
    showWhere();
    drawFlags();
    lines();
  }

  // Kind decides which sections exist, so it alone rebuilds the body. The
  // focus is put back.
  async function redrawForKind() {
    const active = document.activeElement;
    const selector = active?.id ? `#${CSS.escape(active.id)}`
      : active?.type === "radio" ? `[name="${CSS.escape(active.name)}"]:checked`
      : active?.name ? `[name="${CSS.escape(active.name)}"]` : null;
    try {
      [child, items, photos] = await Promise.all([
        api(path), api(`${path}/items`), api(`${path}/photos`)]);
    } catch (error) { failed(error.message); return; }
    render();
    const again = selector && dialog.querySelector(selector);
    if (again) again.focus();
  }

  // Nothing with content stays folded. Only ever opens: never close a section
  // somebody opened.
  function unfoldFilled() {
    for (const section of editorSections(child, items, shapeOf(), photos)) {
      const fold = dialog.querySelector(`[data-fold="${section.key}"]`);
      if (fold && section.open) fold.open = true;
    }
  }

  function showWhere() {
    const shape = shapeOf();
    const line = document.getElementById("child-where");
    const inside = child.parent;
    const room = inheritedRoom(child.path);
    const said = [`A ${shape.label.toLowerCase()}`, inside ? `inside ${inside.code}` : null].filter(Boolean).join(" ");
    line.textContent = room?.room
      ? `${said}, going where ${room.code} goes.`
      : `${said}.`;
  }

  function drawFlags() {
    for (const chip of dialog.querySelectorAll("[data-child-flag]")) {
      const on = Boolean(child[chip.dataset.childFlag]);
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", String(on));
    }
  }

  async function toggleFlag(chip) {
    const key = chip.dataset.childFlag;
    try {
      const fresh = await busy(chip, chip.textContent, () =>
        api(path, { method: "PATCH", body: JSON.stringify({ [key]: !child[key] }) }));
      child = { ...child, ...fresh };
      drawFlags();
      onSaved();
      if (key === "fragile" && fresh.fragile) {
        const marked = await offerFragileClimb(fresh);
        // Only a redraw shows the new chips on the container's page behind.
        if (marked.length) requestRefresh();
      }
    } catch (error) { failed(error.message); }
  }

  // A change from elsewhere (another phone, a finished photo reading), taken in
  // place: no field that is focused or still owed to the server is touched.
  async function absorb() {
    let fresh;
    let freshItems;
    let freshPhotos;
    try {
      [fresh, freshItems, freshPhotos] = await Promise.all([
        api(path), api(`${path}/items`), api(`${path}/photos`)]);
    } catch { return; }   // a failed background refresh leaves it alone
    if (!dialog.isConnected) return;
    const wasKind = child.kind;
    child = { ...child, ...fresh };
    items = freshItems;
    const hadPhotos = photos.length;
    photos = freshPhotos;
    for (const [key, field] of [["content_summary", "child-what"], ["source_location", "child-source-location"]]) {
      const input = document.getElementById(field);
      if (!input || auto.state(key) !== "clean" || document.activeElement === input) continue;
      input.value = fresh[key] || "";
      input.dataset.initial = input.value;
      auto.track(key, tidy(input));
    }
    for (const key of ["kind", "size", "source_room_id"]) {
      const row = dialog.querySelector(`.seg[data-name="${key}"]`);
      if (!row || auto.state(key) !== "clean" || row.contains(document.activeElement)) continue;
      choose(row, fresh[key] ?? "");
      for (const radio of row.querySelectorAll("input[type=radio]")) radio.dataset.initial = String(radio.checked);
      auto.track(key, chosen(row));
    }
    contents?.draw(items);
    // In place, so an open viewer survives. Only the first photo rebuilds the
    // body: until then there is no photo section.
    strip?.draw(photos);
    if (!hadPhotos && photos.length && !dialog.contains(document.activeElement)) { render(); return; }
    unfoldFilled();
    showWhere();
    drawFlags();
    lines();
    // Rebuilding takes the focus, so a new kind waits until nobody is mid-edit.
    if (fresh.kind !== wasKind && !auto.unsaved() && !dialog.contains(document.activeElement)) {
      await redrawForKind();
    }
  }

  const watching = (event) => {
    if (affects(event, { name: "box", code, clientId })) absorb();
  };
  alsoWatching.add(watching);

  // Closing commits what is waiting and waits for it to land, as leaving a
  // record does, so the row behind is right once this is gone.
  let closing = false;
  async function dismiss() {
    if (closing) return;
    closing = true;
    auto.commitAll();
    await auto.idle();
    leaveRecord(session);
    dialog.close();
  }
  // Every way out goes through `dismiss`; the native close would not commit.
  // `cancel` covers a close request that is not a key press.
  closesOnEscape(dialog, () => dismiss());
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); dismiss(); });
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dismiss(); });
  // The link to the whole page, or a scan landing elsewhere.
  const leaving = () => dismiss();
  addEventListener("hashchange", leaving);
  dialog.addEventListener("close", () => {
    alsoWatching.delete(watching);
    removeEventListener("hashchange", leaving);
    if (openEditor === dialog) openEditor = null;
    dialog.remove();
    onSaved();
  });

  render();
  dialog.showModal();
}

// --- looking at one photo ------------------------------------------------------
//
// What the model saw in *that* photo, so a wrong item can be traced to it.
// `showing` lets the strip repaint an open viewer when a reading lands.
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
      <p class="meta by" hidden></p>
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
    const by = dialog.querySelector(".by");
    setText(by, seen.by);
    by.hidden = !seen.by;
    // textContent: the names are the model's words.
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

  // The result arrives later through photos.changed and `showing`. busy() puts
  // the button's words back when done, so the render comes after it, not inside.
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
  const opener = document.activeElement;
  // A click on the backdrop targets the dialog itself.
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => {
    if (showing.render === render) { showing.id = null; showing.render = null; }
    dialog.remove();
    // Back to the thumbnail, if a redraw of the strip has not replaced it.
    if (opener?.isConnected) opener.focus();
  });
  document.body.append(dialog);
  closesOnEscape(dialog, () => dialog.close());
  dialog.showModal();
}

// --- parts of a box page that update on their own -------------------------
//
// The contents list and the photo strip change while the page is open, often
// from a background photo reading while somebody types in the summary. A
// whole-page redraw is held while a field is focused, so these are drawn by
// `reconcile`: the first draw and every update share one path, and a surviving
// row keeps its element, listeners, focus and loaded <img>.
//
// Listeners are attached where a row is created, so there is no rebind step to
// forget. Rows use textContent; the photo figure is markup, and every value in
// it goes through escape().

// `changed(items)` keeps what depends on the list in step (the delete note, the
// print warning). `stale()` is called when a refresh finds its list gone from
// the page: a redraw overtook it, possibly with older data.
function itemsPart(list, { path, changed, stale }) {
  let latest = 0;

  function draw(items) {
    reconcile(list, items, { key: (item) => item.id, create: itemRow, update: fillItemRow });
  }

  async function refresh() {
    // Only the refresh that started last may paint: it holds the newest answer,
    // whatever order the replies arrive in.
    const mine = ++latest;
    const items = await api(`${path}/items`);
    if (mine !== latest) return;
    if (!list.isConnected) { stale(); return; }
    draw(items);
    changed(items);
  }

  function itemRow(item) {
    const row = document.createElement("li");
    // How reconcile finds the row; without it every update draws a second list.
    row.dataset.key = item.id;
    const what = document.createElement("span");
    what.className = "what";
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
    // Deliberately unconfirmed: one tap to re-add, and a modal per row would
    // make tidying what a photo found miserable.
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
    // Handlers read this at tap time, so they act on the item as it is now.
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

  // Enter or leaving saves, Escape puts the name back. The field is *removed*
  // on closing, not hidden: a leftover input differing from what was drawn
  // reads as an unsaved edit to the live-refresh hold, which would then hold
  // the page forever.
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

    // Closing, and the error dialog, move the focus and fire blur, which would
    // save again.
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
      // At once, so the old name does not flash back during the refetch.
      setText(name, value);
      close(refocus);
      // Renaming made the item a person's; the refetch clears "autogenerated".
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

// Module-level so show() can stop it.
let ringTimer = null;

function stopRings() {
  clearInterval(ringTimer);
  ringTimer = null;
}

const OVERDUE_ASK_MS = 5000;

// `contents`: whether this kind holds contents at all; if not, its photos are
// never read. `ask()` requests a refresh through the usual gate. `finished()`
// is called when a job running on the last draw is done on this one.
function photosPart(strip, { path, contents, changed, stale, ask, finished }) {
  // Figures are updated in place, so the viewer reads the photo as last heard,
  // not as the figure was built.
  const lastHeard = new WeakMap();
  let latest = 0;
  let asked = 0;
  // figure -> the snapshot it counts down from, and when that arrived: the
  // server's time left is as of its reply.
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
      // Reduced motion: the text still counts, but once a second and with no
      // easing (.ring-arc), so the ring steps rather than sweeps.
      const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
      ringTimer = setInterval(tick, calm ? 1000 : 200);
    }
    // If `items.changed` and `box.updated` were lost in a reconnect, this is the
    // only sign the list and summary moved. A duplicate costs two GETs.
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
    // Past the estimate: if the "done" event was dropped the spinner would turn
    // forever, so ask now and then while it turns.
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
    // With no honest fraction the inline offset is dropped and the stylesheet's
    // spinner takes over, so the ring is never left sitting full.
    ring.querySelector(".ring-arc").style.strokeDashoffset =
      view.fraction === null ? "" : String(100 * (1 - view.fraction));

    // Retry after a failure, or the first reading of a photo never read.
    const again = block.querySelector("[data-analyse]");
    const offered = view.retry || (view.state === "none" && contents);
    again.hidden = !offered;
    // busy() owns the label while a request is out.
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
    // Everything that can change is here from the start and toggled with
    // `hidden`, so an update never creates a control that needs a listener.
    figure.innerHTML = `
      <div class="pic">
        <a href="/photos/${escape(id)}/full" target="_blank" rel="noreferrer">
          <img src="${escape(photo.thumb)}" srcset="${escape(photo.srcset || "")}"
               sizes="${STRIP_SIZES}" alt="${escape(photo.caption || "Box contents")}"
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

    // Still a real link for a long press or middle click; a plain tap opens the viewer.
    figure.querySelector(".pic a").addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      viewPhoto(lastHeard.get(figure) || photo, { readable: contents });
    });

    // Each action redraws the strip only: a page redraw would lose typing elsewhere.
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
// No contents or no destination room: allowed, but asked first. Returns
// whether to go ahead.
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
// A keyboard-wedge reader types what it scans and presses Return: the Code 128
// is the box number, the QR the box URL. A URL can only come from a label, so
// it opens without asking; a bare word is only *shaped* like a code (so is
// "kettle") and is looked up first.
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

// With nothing focused the reader's keys would go nowhere, so they are collected here.
const scanKeys = new KeyBuffer();
document.addEventListener("keydown", async (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  // A field takes its own keys, and an open dialog is a question being asked.
  // A radio or tick box takes no text: counted as a field, a scan while one had
  // the focus would be ignored and the reader's Return would submit its form.
  const typing = event.target instanceof Element
    && event.target.closest(
      "input:not([type=radio]):not([type=checkbox]), textarea, select, [contenteditable]");
  if (typing || document.querySelector("dialog[open]")) return;

  const now = performance.now();
  // Firefox opens quick find on "/" and "'" when nothing is focused, swallowing
  // the rest of a scanned URL. Only during a scan, so a lone "/" still works.
  if (scanKeys.collecting(now) && (event.key === "/" || event.key === "'")) {
    event.preventDefault();
  }

  const scannedText = scanKeys.feed(event.key, now);
  if (scannedText === null) return;
  // The last button tapped may have the focus, and the reader's Return would
  // press it: after "Print label", a scan would spend tape.
  event.preventDefault();
  if (await openEntered(scannedText)) return;
  // Not a box here: search for it, so a mis-scan is visible.
  location.hash = `#/search/${encodeURIComponent(scannedText.trim())}`;
});

// --- views ----------------------------------------------------------------

const boxesPath = (query) =>
  query ? `/search?q=${encodeURIComponent(query)}` : "/boxes?limit=100";

const listHeading = (boxes, query) =>
  query ? `Matches for “${query}”` : `${boxes.length} item${boxes.length === 1 ? "" : "s"}`;

// Rows are updated in place by reconcile, never rebuilt, so a tap that began on
// a row's <a> still opens the box it was aimed at. The thumbnail frame is drawn
// for every row and sized by the stylesheet, so a photo arriving later fills
// its gap rather than pushing the rows below down.
function rowFor(box) {
  const row = document.createElement("li");
  row.dataset.key = box.code;
  const link = document.createElement("a");
  link.setAttribute("href", `#/b/${encodeURIComponent(box.code)}`);
  const frame = document.createElement("span");
  frame.className = "t";
  // Under the photo: an <img> that arrives later paints over it.
  frame.append(iconNode(kindIcon(box), "i tk"));
  const thumb = document.createElement("img");
  thumb.alt = "";  // decorative: the code beside it names the box
  thumb.loading = "lazy";
  thumb.hidden = true;
  frame.append(thumb);
  link.append(frame);
  for (const cls of ["c", "s", "w"]) {
    const span = document.createElement("span");
    span.className = cls;
    link.append(span);
  }
  // The last cell: kind, status, and how much is inside.
  for (const cls of ["k", "st", "in"]) {
    const line = document.createElement("span");
    line.className = cls;
    link.lastElementChild.append(line);
  }
  row.append(link);
  return row;
}

// `data-context` marks a container shown to say where a match is, not because
// it matched. The stylesheet caps the indent `depth` gives.
function placeRow(row, { depth = 0, context = false } = {}) {
  if (depth) row.style.setProperty("--depth", depth);
  else row.style.removeProperty("--depth");
  row.toggleAttribute("data-context", context);
}

function fillRow(row, box) {
  const [code, summary, where] = row.querySelectorAll("span.c, span.s, span.w");
  setText(code, box.code);
  setText(summary, box.content_summary || "Nothing written down yet");
  const said = rowStatus(box);
  setText(where.querySelector(".k"), said.kind);
  setText(where.querySelector(".st"), said.status);
  setText(where.querySelector(".in"), said.inside);
  setIcon(row.querySelector("span.t .tk"), kindIcon(box));
  setThumb(row.querySelector("span.t img"), coverUrl(box));
}

// Removes src rather than emptying it: an empty src re-requests the page itself.
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

// Search results carry the containers of matches (`matched` false) and are
// drawn grouped; groupMatches returns any other list flat.
const patchBoxList = (list, boxes) =>
  reconcile(list, groupMatches(boxes), {
    key: (found) => found.row.code,
    create: (found) => rowFor(found.row),
    update: (node, found) => {
      fillRow(node, found.row);
      placeRow(node, found);
    },
  });

// A background refresh checks this before painting: a tap's navigation can
// finish before a refetch begun earlier, which would paint the list over the
// box just opened.
const here = () => location.hash || "#/";

async function refreshBoxes(query, at) {
  const boxes = await api(boxesPath(query));
  if (here() !== at) return;
  const list = document.getElementById("boxlist");
  if (!list || !boxes.length) {
    // Into or out of the empty state changes the whole page.
    await viewBoxes(query);
    return;
  }
  patchBoxList(list, boxes);
  setText(document.getElementById("list-heading"), listHeading(boxes, query));
}

async function viewBoxes(query) {
  const boxes = await api(boxesPath(query));

  const list = boxes.length
    ? `<ul class="boxlist" id="boxlist"></ul>`
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

  if (boxes.length) patchBoxList(document.getElementById("boxlist"), boxes);

  document.getElementById("search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("q").trim();
    // A barcode reader types a code or a box URL here and presses Return.
    if (await openEntered(value)) return;
    location.hash = value ? `#/search/${encodeURIComponent(value)}` : "#/";
  });

  watch({ name: "list", query, refresh: (at) => refreshBoxes(query, at) });
}

// An event arriving while a whole-page draw is fetching may be applied to the
// page about to be replaced, and the replacement may predate it. So draws are
// counted, and a part that changed meanwhile is asked for again (`overtaken`).
async function viewBox(code, options) {
  // Pending saves are sent *and waited for*: a page fetched before a save
  // lands would show the old text and adopt it as the baseline. What cannot be
  // saved at all is put back into its field by the new page.
  const open = sessions.get(code);
  if (open) {
    open.auto.commitAll();
    await open.auto.idle();
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
  // Kept up to date in place; the delete note and the print override read them.
  let box = drawnBox;
  let items = drawnItems;
  let photos = drawnPhotos;
  const shape = allKinds.find((k) => k.kind === box.kind) || allKinds[0];
  // A background refresh gives up if the page changed while it fetched;
  // foreground calls pass no `at`.
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
    <nav class="trail" id="trail" aria-label="Inside" hidden></nav>
    <h1 class="code">${escape(box.code)}</h1>
    ${flags.length ? `<div class="flags">${flags.map((f) => `<span class="flag">${iconMarkup(flagIcon(f.key))}${escape(f.label)}</span>`).join("")}</div>` : ""}
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
        <div data-seg="kind"></div>
        ${shape.sizes?.length ? '<div data-seg="size"></div>' : ""}
        <div data-seg="destination_room_id"></div>
        <p class="meta" id="goes-with" hidden></p>
        <div data-seg="source_room_id"></div>
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
              aria-pressed="${box[key] ? "true" : "false"}">${iconMarkup(flagIcon(key))}${escape(label)}</button>`).join("")}
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

    <div class="section">
      <h2>What it is inside</h2>
      <form id="container">
        <p class="meta" id="inside-of"></p>
        <input type="hidden" name="parent_code" value="${escape(box.parent?.code || "")}">
        <label class="dlabel" for="container-code">Put it inside a container: type its code, or scan its label</label>
        <div class="row">
          <input id="container-code" name="parent_lookup" placeholder="B-0012"
                 autocapitalize="characters" autocomplete="off" enterkeyhint="go">
          <button class="btn quiet" type="submit" id="container-look">Look up</button>
        </div>
        <p class="meta" id="container-found" hidden></p>
        <div class="row" id="container-acts" hidden>
          <button class="btn" type="button" id="container-put"></button>
        </div>
        <div class="row" id="container-out" hidden>
          <button class="btn quiet" type="button" id="container-take">Take it out</button>
        </div>
        ${AUTOSAVE_LINE}
      </form>
    </div>

    ${!shape.contents ? "" : `
    <div class="section" id="inside-section">
      <h2>Inside this ${escape(shape.label.toLowerCase())}</h2>
      <p class="meta" id="inside-empty" hidden>Nothing inside yet.</p>
      <ul class="boxlist" id="inside"></ul>
      <div class="row" style="margin-top:0.75rem">
        <button class="btn quiet" type="button" id="add-inside">Add something inside</button>
      </div>
      <p class="meta" id="inside-said" role="status" hidden></p>
    </div>

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
               value="${escape(shape.copies ?? 1)}" aria-label="Copies"
               style="flex:0 0 4.5rem;text-align:center">
      </div>
      <p class="meta">Copies. A ${escape(shape.label.toLowerCase())} prints
         ${escape(shape.copies ?? 1)} unless you say; that number is set in Settings.</p>
    </div>

    ${box.deleted_at ? "" : `
    <div class="section danger">
      <h2>Delete</h2>
      <p class="meta" id="delete-note"></p>
      <p class="meta" id="delete-blocked" hidden></p>
      <div class="row" id="delete-row">
        <button class="btn quiet" id="delete">Delete this ${escape(shape.label.toLowerCase())}</button>
      </div>
    </div>`}`);

  for (const button of app.querySelectorAll("[data-flag]")) {
    button.addEventListener("click", () => {
      const key = button.dataset.flag;
      act(async () => {
        const fresh = await api(path, { method: "PATCH", body: JSON.stringify({ [key]: !box[key] }) });
        if (key === "fragile" && fresh.fragile) await offerFragileClimb(fresh);
      });
    });
  }

  for (const button of app.querySelectorAll("[data-status]")) {
    button.addEventListener("click", () => act(() =>
      api(`/boxes/${encodeURIComponent(code)}/status`, { method: "POST", body: JSON.stringify({ status: button.dataset.status }) })));
  }

  const page = here();
  // A refresh that finds its element gone was overtaken by a redraw that may
  // predate its change: ask again, unless the person has left the page.
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

  const kindRow = mount(segmented({ name: "kind", legend: "This is a", options: kindChoices(allKinds), value: box.kind }));
  if (shape.sizes?.length) {
    mount(segmented({
      name: "size", legend: "How big", options: sizeChoices(shape.sizes),
      value: box.size, optional: true, empty: "No size" }));
  }
  // Always built, even when nested and hidden: it comes and goes in place.
  const roomRow = mount(segmented({
    name: "destination_room_id", legend: "Destination room", options: roomChoices(forDestination(rooms)),
    value: box.destination_room_id, optional: true, empty: "Not decided yet" }));
  mount(segmented({
    name: "source_room_id", legend: "Packed from", options: roomChoices(forSource(rooms)),
    value: box.source_room_id, optional: true, empty: "Not recorded" }));

  // --- the forms that save themselves ---
  const summaryForm = document.getElementById("summary-form");
  const destinationForm = document.getElementById("destination");
  const locationForm = document.getElementById("location");
  const containerForm = document.getElementById("container");
  const summaryField = summaryForm.querySelector("[name=content_summary]");
  const savingForms = [summaryForm, destinationForm, locationForm, containerForm];

  const session = editSession(code);
  const auto = session.auto;

  // Nested, it goes where the nearest container with a room goes. Its own room
  // stays in the database for when it is taken out.
  const goingTo = () => {
    const from = inheritedRoom(box.path);
    return from ? from.room : box.destination_room_id;
  };

  function showRoom() {
    const now = rooms.find((r) => r.id === goingTo());
    const band = document.getElementById("room-band");
    setText(band, now?.name || "");
    band.hidden = !now;

    const from = inheritedRoom(box.path);
    roomRow.hidden = Boolean(from);
    // A disabled fieldset's radios stay out of FormData and fire nothing.
    roomRow.disabled = Boolean(from);
    const line = document.getElementById("goes-with");
    line.hidden = !from;
    if (!from) return;
    const link = document.createElement("a");
    link.setAttribute("href", `#/b/${encodeURIComponent(from.code)}`);
    link.textContent = from.code;
    line.replaceChildren("Goes where ", link, now ? ` goes: ${now.name}.` : " goes; no room chosen for it yet.");
  }

  // --- things inside things ---
  //
  // What it is inside and what is inside it come with the record and change on
  // box.updated, so they are redrawn from `box` in place, never with the page.
  let children = box.children || [];
  const singles = allKinds.filter((k) => !k.contents).map((k) => k.kind);

  function showInside() {
    const nav = document.getElementById("trail");
    const steps = trail(box.path);
    nav.replaceChildren(...steps.flatMap((step) => {
      const link = document.createElement("a");
      link.setAttribute("href", `#/b/${encodeURIComponent(step.code)}`);
      link.title = step.hint;
      link.textContent = step.code;
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.setAttribute("aria-hidden", "true");
      sep.textContent = "›";
      return [link, sep];
    }));
    if (steps.length) {
      const now = document.createElement("span");
      now.setAttribute("aria-current", "page");
      now.textContent = "this";
      nav.append(now);
    }
    nav.hidden = !steps.length;

    const line = document.getElementById("inside-of");
    const parent = box.parent;
    if (parent) {
      const link = document.createElement("a");
      link.setAttribute("href", `#/b/${encodeURIComponent(parent.code)}`);
      link.textContent = parent.code;
      // One span: the line is a flex row, and a flex container drops the spaces
      // around bare text runs.
      const said = document.createElement("span");
      said.append("Inside ", link, ` (${describe(parent)}).`);
      line.replaceChildren(said);
    } else {
      line.textContent = "Not inside anything.";
    }
    document.getElementById("container-out").hidden = !parent;
    showRoom();
  }

  function drawChildren() {
    const list = document.getElementById("inside");
    if (!list) return;   // a single thing: nothing goes inside it
    patchBoxList(list, children);
    document.getElementById("inside-empty").hidden = children.length > 0;
    // A container holding things may neither become a single thing nor be
    // deleted: the server refuses both, so neither is offered.
    restrict(kindRow, children.length ? singles : [],
      "It holds things: move them out before making it a single thing.");
    const blocked = blockedDelete(children);
    const note = document.getElementById("delete-blocked");
    if (note) {
      setText(note, blocked);
      note.hidden = !blocked;
      document.getElementById("delete-row").hidden = Boolean(blocked);
    }
  }
  showInside();
  drawChildren();

  // Its dialog is on document.body, like every dialog here: a live refresh
  // underneath cannot take it away, and the hold never counts its fields.
  document.getElementById("add-inside")?.addEventListener("click", () => {
    addInside({ parent: box, kinds: allKinds, rooms, shape });
  });

  // A plain tap opens the editor over this page; the row is still a real link
  // for a middle click, long press or modifier. Delegated, so reconciled rows
  // need no rebinding.
  document.getElementById("inside")?.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href^='#/b/']");
    if (!link) return;
    event.preventDefault();
    editSubitem(decodeURIComponent(link.getAttribute("href").slice(4)), {
      kinds: allKinds,
      rooms,
      // The rows come from the container's own record.
      onSaved: () => requestPart("summary"),
    });
  });

  // A container is typed or scanned (the reader's Return submits), looked up
  // and previewed before anything is saved. The autosaver tracks the hidden
  // `parent_code` field, with Undo like any other.
  const lookup = document.getElementById("container-code");
  const found = document.getElementById("container-found");
  const acts = document.getElementById("container-acts");
  const put = document.getElementById("container-put");
  let candidate = null;
  const offer = (message, warn) => {
    setText(found, message);
    found.hidden = !message;
    found.classList.toggle("warn", Boolean(warn));
    acts.hidden = !candidate;
  };
  containerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    candidate = null;
    // A scanned QR is the record's URL; the Code 128 and a typed code are bare.
    const typed = lookup.value.trim();
    const wanted = (entered(typed)?.code || typed).toUpperCase();
    if (!wanted) { offer("", false); return; }
    const look = document.getElementById("container-look");
    let target = null;
    try {
      target = await busy(look, "Looking…", () => api(`/boxes/${encodeURIComponent(wanted)}`));
    } catch (error) {
      offer(error.status === 404 ? `There is no ${wanted}.` : error.message, true);
      return;
    }
    const verdict = mayHold(target, box, children, allKinds);
    if (!verdict.ok) { offer(verdict.why, true); return; }
    candidate = target;
    setText(put, `Put it inside ${target.code}`);
    offer(`${target.code}: ${describe(target)}.`, false);
  });
  put.addEventListener("click", () => {
    if (!candidate) return;
    auto.edit("parent_code", candidate.code, "change");
    session.undoAt = containerForm.id;
    candidate = null;
    offer("", false);
    lookup.value = "";
    lookup.dataset.initial = "";
    drawLines();
  });
  document.getElementById("container-take").addEventListener("click", () => {
    auto.edit("parent_code", "", "change");
    session.undoAt = containerForm.id;
    drawLines();
  });

  // Kind decides which sections exist, so it alone redraws the page. The focus
  // goes back, caret included: the redraw is the page's doing, not the person's.
  async function redrawForKind() {
    const active = document.activeElement;
    // A pushbutton row's tab stop is its chosen button; by name alone the
    // first button would get the focus.
    const selector = active?.id ? `#${CSS.escape(active.id)}`
      : active?.type === "radio" ? `[name="${CSS.escape(active.name)}"]:checked`
      : active?.name ? `[name="${CSS.escape(active.name)}"]` : null;
    let caret = null;
    try { caret = [active.selectionStart, active.selectionEnd]; } catch { /* not a text field */ }
    await viewBox(code, { keepBanner: true });
    const again = selector && app.querySelector(selector);
    if (!again || document.activeElement === again) return;
    again.focus();
    try { if (caret?.[0] != null) again.setSelectionRange(...caret); } catch { /* a picker */ }
  }

  const { drawLines, onReturn } = autosaveFields({
    root: app,
    session,
    forms: savingForms,
    undoLabel: (key) => (key === "content_summary" && !shape.contents ? "name" : AUTOSAVED.get(key)),
    onLanded(key, value, fresh) {
      box = {
        ...box, [key]: fresh[key], summary_source: fresh.summary_source, updated_at: fresh.updated_at,
        parent: fresh.parent, path: fresh.path, children: fresh.children,
      };
      if (key === "destination_room_id") showRoom();
      if (key === "parent_code") {
        showInside();
        // The climb marks this page's copy of the path, so it does not ask twice.
        if (box.fragile && box.parent) offerFragileClimb(box).catch((error) => failed(error.message));
      }
    },
    onKindSaved: redrawForKind,
  });
  summaryForm.addEventListener("submit", onReturn);
  destinationForm.addEventListener("submit", onReturn);
  locationForm.addEventListener("submit", onReturn);

  drawLines();
  wireDictation(app);

  // `box.updated`, applied in place when possible. A finished photo sends it
  // after rebuilding the summary, where somebody may be typing.
  async function refreshSummary() {
    const fresh = await api(path);
    if (!summaryField.isConnected) { stale("summary")(); return; }
    // A move at either end sends box.updated; these touch no field.
    children = fresh.children || [];
    box = { ...box, children, path: fresh.path, parent: fresh.parent };
    drawChildren();
    showInside();
    recount();
    const incoming = fresh.content_summary || "";
    const moved = incoming !== (summaryField.dataset.initial ?? "");
    const inUse = auto.state("content_summary") !== "clean" || hasUnsavedEdits([{
      value: summaryField.value,
      initial: summaryField.dataset.initial ?? "",
      focused: document.activeElement === summaryField,
    }]);
    // Flags, rooms or kind moved, or the summary moved under somebody's
    // cursor: the whole-page refresh decides, and never clobbers typing.
    const needsPage = !onlySummaryChanged(box, fresh) || (moved && inUse);
    if (moved && !inUse) {
      // All three, so the field stays pristine. Without `initial` every later
      // refresh is held; without track() the saver sends the server's text
      // back as this person's, and photos never update it again.
      summaryField.value = incoming;
      summaryField.dataset.initial = incoming;
      auto.track("content_summary", tidy(summaryField));
      box = { ...box, content_summary: fresh.content_summary, summary_source: fresh.summary_source };
      recount();
    }
    if (needsPage) requestRefresh();
    else box = fresh;
  }
  // Absent for a single thing.
  const addForm = document.getElementById("add-item");
  addForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    // Dictation arrives as one run-on phrase.
    const names = splitItems(new FormData(event.target).get("name"));
    if (!names.length) return;
    const button = addForm.querySelector("button[type=submit]");
    try {
      await busy(button, `Adding ${names.length}…`, async () => {
        for (const name of names) {
          await api(`${path}/items`, { method: "POST", body: JSON.stringify({ name }) });
        }
      });
      // Only once every name is in, so a failure leaves the text to resend.
      addForm.reset();
    } catch (error) { failed(error.message); }
    // The list alone, and either way: a failure part-way still added some.
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
      // Saves at once, like a picker; Undo puts the old summary back.
      field.value = summary;
      auto.edit("content_summary", tidy(field), "change");
      session.undoAt = summaryForm.id;
      drawLines();
    } catch (error) { failed(error.message); }
  });

  // Counted when asked, not at draw time: items and photos change under an open page.
  const losses = () => [
    items.length && `${items.length} item${items.length === 1 ? "" : "s"}`,
    photos.length && `${photos.length} photo${photos.length === 1 ? "" : "s"}`,
  ].filter(Boolean);
  const printed = box.label_print_count || 0;

  // Whatever outside the two parts depends on what they hold.
  function recount() {
    const losing = losses();
    const note = document.getElementById("delete-note");
    if (note) {
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
        box.parent ? `It is inside ${box.parent.code}; it leaves there, and search, on every device.`
          : "It leaves the list and search on every device.",
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
  // Copies is for this print only and never saved, so its baseline follows its
  // value: otherwise it would read as half-edited and hold live updates forever.
  const copiesField = document.getElementById("copies");
  copiesField.addEventListener("input", () => { copiesField.dataset.initial = copiesField.value; });

  printButton.addEventListener("click", async () => {
    const contents = hasContents(box, items);
    const sure = await confirmThinLabel(code, { contents, room: Boolean(goingTo()) });
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
        // No tape came out; "Printed" would get the button pressed again.
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
      await api(`${path}/photos`, { method: "POST", body });
      await strip.refresh();
    } catch (error) { failed(error.message); }
    finally {
      note.hidden = true;
      picker.disabled = false;
      // Otherwise choosing the same file again would not fire "change".
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
    // Updated without redrawing the page; partOf in live.js maps events to parts.
    parts: {
      items: () => contents?.refresh(),
      photos: () => strip.refresh(),
      summary: refreshSummary,
    },
  });
}

async function viewNew(parentCode = null) {
  const [rooms, allKinds, parent] = await Promise.all([
    api("/rooms"),
    api("/settings/kinds"),
    // An unknown container code is not fatal: the form is drawn on its own.
    parentCode ? api(`/boxes/${encodeURIComponent(parentCode)}`).catch(() => null) : null,
  ]);
  // Inside a container plain Create comes first: nested things rarely get a label.
  const creates = parent
    ? `<button class="btn" type="submit" id="create">Create</button>
       <button class="btn quiet" type="submit" id="create-stub" data-print="stub" style="margin-top:0.75rem">Create and print stub</button>
       <button class="btn quiet" type="submit" id="create-print" data-print="label" style="margin-top:0.5rem">Create and print label</button>`
    : `<button class="btn" type="submit" id="create-stub" data-print="stub">Create and print stub</button>
       <p class="meta" style="margin:0.35rem 0 0">One inch of tape: just the number and the QR, to stick on before you pack.</p>
       <button class="btn quiet" type="submit" id="create" style="margin-top:0.75rem">Create</button>
       <button class="btn quiet" type="submit" id="create-print" data-print="label" style="margin-top:0.5rem">Create and print label</button>`;
  show(`
    <h1 class="code">New</h1>
    ${parent ? `
      <p class="say" id="inside-note">Inside <a href="#/b/${escape(encodeURIComponent(parent.code))}">${escape(parent.code)}</a>
        (${escape(describe(parent))}).<br><a href="#/new" id="not-inside">Make it on its own instead</a></p>` : ""}
    <form id="new">
      <div class="section">
        <h2>What it is</h2>
        <div data-seg="kind"></div>
        <div data-seg="size"></div>
      </div>
      <div class="section">
        <h2>Where it is going</h2>
        <div data-seg="destination_room_id"></div>
      </div>
      <div class="section">
        <h2>What is in it, or what it is</h2>
        <textarea name="content_summary" rows="3"
          placeholder="pots, baking pans, stand mixer &mdash; or Bicycle"></textarea>
        <div data-seg="source_room_id"></div>
        <input name="source_location" placeholder="Where in that room (optional)"
               style="margin-top:0.5rem">
        <label style="display:flex;gap:0.6rem;align-items:center;margin-top:0.75rem">
          <input type="checkbox" name="fragile" style="width:auto;min-height:auto"> Fragile
        </label>
      </div>
      <div class="section">
        ${creates}
      </div>
    </form>`);
  if (parentCode && !parent) {
    announce(`There is no ${parentCode} to put this inside; it will be on its own.`, { warn: true });
  }

  // Enter must never spend tape. A browser submits with the *first* submit
  // button, which prints the stub, so Enter is pointed at plain Create.
  document.getElementById("new").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
    event.preventDefault();
    // Not from a pushbutton: a barcode reader's Return with one focused would
    // create an empty record.
    if (event.target.type === "radio") return;
    event.currentTarget.requestSubmit(document.getElementById("create"));
  });

  const first = allKinds[0];
  mount(segmented({ name: "kind", legend: "Kind", options: kindChoices(allKinds), value: first.kind }));
  const allSizes = allKinds.find((k) => k.sizes?.length)?.sizes || [];
  const sizeRow = mount(segmented({
    name: "size", legend: "How big", options: sizeChoices(allSizes), optional: true, empty: "No size" }));
  // Inside a container, it goes where the container goes.
  const roomSlot = app.querySelector('[data-seg="destination_room_id"]');
  if (parent) roomSlot.closest(".section").remove();
  else {
    mount(segmented({
      name: "destination_room_id", legend: "Destination room", options: roomChoices(forDestination(rooms)),
      optional: true, empty: "Not decided yet" }));
  }
  mount(segmented({
    name: "source_room_id", legend: "Packed from", options: roomChoices(forSource(rooms)),
    optional: true, empty: "Not recorded" }));

  const form = document.getElementById("new");
  const kindChosen = () => allKinds.find((k) => k.kind === new FormData(form).get("kind")) || first;
  // Disabled as well as hidden: a disabled fieldset's radios stay out of
  // FormData, so a box's size is not sent with the furniture it became.
  const followKind = () => {
    const shape = kindChosen();
    document.getElementById("create").textContent = `Create ${shape.label.toLowerCase()}`;
    const sized = Boolean(shape.sizes?.length);
    sizeRow.hidden = !sized;
    sizeRow.disabled = !sized;
  };
  form.addEventListener("change", (event) => { if (event.target.name === "kind") followKind(); });
  followKind();

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
    const size = form.get("size");
    if (size) payload.size = size;
    if (parent) payload.parent_code = parent.code;

    // data-print is "stub", "label", or absent.
    const pressed = event.submitter || document.getElementById("create");
    const printing = pressed.dataset.print || null;
    const wantsLabel = printing !== null;

    // Asked *before* creating, so no leaves nothing made. The stub is exempt:
    // an empty box is what it is for.
    if (printing === "label") {
      const sure = await confirmThinLabel("its label", {
        contents: Boolean(payload.content_summary),
        room: Boolean(payload.destination_room_id || inheritedRoom([...(parent?.path || []), parent].filter(Boolean))?.room),
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
      if (payload.fragile && box.path?.length) await offerFragileClimb(box);
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
// The server says only *which box* changed; a refresh refetches through the
// endpoints the first draw used, so a dropped or duplicated notification costs
// a GET and nothing more.
//
// A refresh is an interruption: it can take the keyboard mid-word, discard an
// unsaved edit, or move a row under a thumb coming down on it. So it is held
// while a field is focused or dirty, a pointer is down, and for the settle
// after, and a held refresh says so. A box page's `parts` (contents, photos,
// summary) touch no field being typed in, so they wait only for the gesture.

// The view on screen. The new-box form and the scanner are never refreshed:
// `affects` matches nothing for them.
let view = null;
let pending = false;
let flushTimer = null;

// Parts of the open box waiting for a still moment.
const waiting = new Set();
let partTimer = null;

// Whole-page draws in flight, and the parts that changed during one (viewBox).
let drawing = 0;
const overtaken = new Set();

// `lastTouch` holds the screen still after the finger lifts: the click has not
// landed yet.
let pointerDown = false;
let lastTouch = 0;

function watch(next) {
  view = { ...next, clientId, at: here() };
  pending = false;
  showNotice();
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
  // A hidden page catches up in LiveChannel.wake.
  if (document.hidden) { pending = true; return; }
  if (holdRefresh(holdState())) {
    pending = true;
    showNotice();
    scheduleFlush();
    return;
  }
  runRefresh();
}

async function runRefresh() {
  if (!view || !view.refresh) return;
  pending = false;
  showNotice();
  const target = view;
  try {
    await target.refresh(target.at);
  } catch {
    // A failed background refresh leaves a working page alone.
  }
}

// Unlike a whole refresh this goes ahead while a field is focused or dirty (the
// summary part checks for itself), but still waits out a gesture: a list that
// grows between pointerdown and click moves the thing being tapped.
function requestPart(part) {
  if (!view || !view.parts || !view.parts[part]) return;
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

// Removing a focused element (the inline rename field) fires no focusout, so
// that is announced by hand.
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
  // Still mid-edit: the notice stays up. The focusout/change listeners wake
  // this again, not a timer ticking forever behind a focused field.
  if (hold.editing) return;
  if (holdRefresh(hold)) { scheduleFlush(); return; }
  runRefresh();
}

const notice = document.getElementById("live");
// One banner, two reasons; a newer app wins, since a reload brings everything up to date.
notice.addEventListener("click", () => (updateTo ? reloadWhenSafe("asked") : runRefresh()));

function showNotice() {
  if (updateTo) {
    notice.textContent = askedToReload
      ? "App updated — reloading once everything is saved"
      : "App updated — tap to reload";
    notice.hidden = false;
    return;
  }
  notice.textContent = "Changed on another device — tap to refresh";
  notice.hidden = !pending;
}

// --- keeping up with deploys ------------------------------------------------
//
// An open page runs the old app.js until reloaded. reload.js decides; this asks
// and acts. Checked when the socket reconnects (a deploy drops it), when the
// page becomes visible, and every CHECK_MS while visible.
//
// An idle page reloads itself. One in use waits for what a refresh waits for,
// plus an open dialog, a write in flight and every session's saves; the banner
// offers it meanwhile. Never on becoming hidden: a hidden page can be frozen
// before its saves land, and would lose a photo taken in the add dialog.

// Null on a dev server, which serves modules unversioned: none of this runs,
// or it would reload forever.
const running = runningRevision(import.meta.url);
let updateTo = null;         // a newer revision the server is running, once seen
let offerOnly = false;       // already reloaded towards it once: offer, never force
let askedToReload = false;   // the banner was tapped and something had to land first
let reloading = false;
let checkTimer = null;

// The accessor itself can throw (a private window, blocked site data).
function tabStorage() {
  try { return sessionStorage; } catch { return undefined; }
}

async function checkRevision() {
  if (!running || document.hidden) return;
  let live = null;
  try {
    // no-store, and the service worker passes /health through: a cached answer
    // would always read as the revision already running.
    const response = await fetch("/health", { cache: "no-store" });
    if (response.ok) live = liveRevision(await response.json());
  } catch { /* down, mid-deploy: cannot tell, which is not "changed" */ }
  const step = nextStep({ running, live, tried: readTried(tabStorage()) });
  if (step === "off" || step === "wait") return;
  updateTo = step === "current" ? null : live;
  offerOnly = step === "offer";
  if (!updateTo) askedToReload = false;
  if (step === "reload") reloadWhenSafe("idle");
  else showNotice();
}

// What a reload would destroy now. `owed` is only asked once saves have been
// committed and waited for.
function reloadState({ settled = false } = {}) {
  return {
    held: holdRefresh(holdState()),
    dialog: Boolean(document.querySelector("dialog[open]")),
    busy: writing > 0,
    owed: settled && Array.from(sessions.values()).some((session) => session.auto.unsaved()),
  };
}

// True once the reload has started, false when it must wait. `moment` is
// "idle", "asked" or "leaving".
async function reloadWhenSafe(moment) {
  if (!updateTo || reloading) return false;
  if (offerOnly && moment !== "asked") { showNotice(); return false; }
  if (moment === "asked") askedToReload = true;
  if (reloadBlocked(reloadState(), moment)) { showNotice(); return false; }
  reloading = true;
  // Send what is waiting in every session and wait for it to land: the new
  // page could otherwise fetch the record before the save and show old text.
  everySession((session) => session.auto.commitAll());
  await Promise.all(Array.from(sessions.values(), (session) => session.auto.idle()));
  const blocked = reloadBlocked(reloadState({ settled: true }), moment)
    // Remembered first, so the new page knows it tried and cannot loop. A tab
    // that cannot remember does not reload by itself.
    || (!rememberTried(tabStorage(), updateTo) && moment !== "asked");
  if (blocked) {
    reloading = false;
    showNotice();
    return false;
  }
  await freshWorker();
  location.reload();
  return true;
}

// A tapped banner's reload, held by a save or a request, goes once that lands.
function reloadIfAsked() {
  if (askedToReload && !reloading) reloadWhenSafe("asked");
}

// The controlling worker answers the reload, and until the handover that is the
// old one serving the old shell. So update and wait for controllerchange first;
// at most 5 s, and any failure lets the reload go ahead.
async function freshWorker() {
  const workers = navigator.serviceWorker;
  if (!workers?.controller) return;
  try {
    const registration = await workers.getRegistration();
    if (!registration) return;
    const handover = new Promise((resolve) => {
      workers.addEventListener("controllerchange", resolve, { once: true });
    });
    await registration.update();
    if (!registration.installing && !registration.waiting) return;
    await Promise.race([handover, new Promise((resolve) => setTimeout(resolve, 5000))]);
  } catch { /* no worker to update: nothing stands in the reload's way */ }
}

// Only while visible: a phone in a pocket does not spend battery asking.
function pollWhileVisible() {
  clearInterval(checkTimer);
  checkTimer = document.hidden ? null : setInterval(checkRevision, CHECK_MS);
}

if (running) {
  pollWhileVisible();
  document.addEventListener("visibilitychange", () => {
    pollWhileVisible();
    checkRevision();
  });
}

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

// Leaving a field, or committing a dropdown, is when a held refresh may go.
for (const kind of ["focusout", "change"]) {
  addEventListener(kind, () => { if (pending) scheduleFlush(); }, { capture: true, passive: true });
}

function socketUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const key = keyStore.get();
  // In the query: a WebSocket handshake cannot carry a header.
  return `${scheme}//${location.host}/api/events${key ? `?key=${encodeURIComponent(key)}` : ""}`;
}

// Watchers besides the view, such as the sub-item modal following its own record.
const alsoWatching = new Set();

const live = new LiveChannel({
  url: socketUrl,
  // A deploy drops the socket; coming back is the first sign of new code.
  onOpen: () => checkRevision(),
  onEvent: (event) => {
    for (const watcher of Array.from(alsoWatching)) watcher(event);
    // Also drops this device's own echo: it already redrew what it changed.
    if (!affects(event, view)) return;
    // `view` can be null here: a resync affects everything, watched or not.
    const part = view?.parts ? partOf(event) : null;
    if (!part) { requestRefresh(); return; }
    if (drawing) overtaken.add(part);
    requestPart(part);
  },
});

async function viewSettings() {
  const [shape, press, rooms, allKinds, spend] = await Promise.all([
    api("/settings/code-format"),
    api("/printer").catch(() => null),
    api("/rooms"),
    api("/settings/kinds"),
    api("/settings/spend").catch(() => null),
  ]);
  const reading = spend ? readingWith(spend) : null;

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
      <h3 class="dlabel" style="margin-top:1rem">Labels for each kind of thing</h3>
      <form id="kind-copies">
        <ul class="items copies">${allKinds.map((k) => `
          <li>
            <label for="copies-${escape(k.kind)}">${escape(k.label)}</label>
            <input id="copies-${escape(k.kind)}" name="${escape(k.kind)}" type="number" min="1" max="10"
                   inputmode="numeric" value="${escape(k.copies)}">
          </li>`).join("")}</ul>
        <div class="row" style="margin-top:0.75rem">
          <button class="btn quiet" type="submit">Save</button>
        </div>
      </form>
      <p class="meta">How many print when nobody says: a box wants a label on
         more than one face, a chair does not. You can still change the number
         for a single print. A stub always prints one.</p>
    </div>

    ${!reading ? "" : `
    <div class="section" data-reading="${escape(reading.state)}">
      <h2>Reading photos</h2>
      <p>${escape(reading.now)}</p>
      <p class="meta">${escape(reading.why)}</p>
      <p class="spend"><span class="spent">${escape(reading.spent)}</span>
         <span class="meta">of ${escape(reading.cap)} spent on ${escape(spend.photos)}
         ${spend.photos === 1 ? "photo" : "photos"}</span></p>
      <div class="gauge" role="img"
           aria-label="${escape(reading.spent)} of ${escape(reading.cap)}">
        <span style="width:${(reading.fraction * 100).toFixed(1)}%"></span>
      </div>
      ${!spend.by_model.length ? "" : `<ul class="items">${spend.by_model.map((m) => `
        <li><span>${escape(m.model)}</span>
            <span class="qty">${escape(money(m.spent_usd))} &middot; ${escape(m.photos)}</span></li>`).join("")}</ul>`}
      <p class="meta">Past the cap nothing more is spent: photos are read on
         this machine instead, and the list keeps filling itself in. The cap
         and the models are set in <code>.env</code>, not here — a budget you
         could raise by brushing a field would not be much of a budget.</p>
    </div>`}

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

  // One request per changed kind, each read back from what the server kept.
  document.getElementById("kind-copies").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = Array.from(event.target.querySelectorAll("input[type=number]"));
    const changed = fields.filter((field) => field.value !== field.dataset.initial);
    if (!changed.length) { announce("Nothing changed."); return; }
    const button = event.target.querySelector("button[type=submit]");
    const said = [];
    try {
      await busy(button, "Saving…", async () => {
        for (const field of changed) {
          const saved = await api("/settings/kind-copies", {
            method: "PUT", body: JSON.stringify({ kind: field.name, copies: Number(field.value) }) });
          field.value = saved.copies;
          field.dataset.initial = String(saved.copies);
          const label = allKinds.find((k) => k.kind === saved.kind)?.label || saved.kind;
          said.push(`${label.toLowerCase()} ${saved.copies}`);
        }
      });
      announce(`Labels per print: ${said.join(", ")}.`);
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
  [/^#\/new$/, () => viewNew()],
  [/^#\/new\/in\/([^/]+)$/, (inside) => viewNew(decodeURIComponent(inside))],
  [/^#\/settings$/, viewSettings],
  [/^#\/deleted$/, viewDeleted],
  [/^#\/scan$/, () => import("/scan.js").then((m) => m.viewScan(show, showError))],
];

async function route() {
  const hash = location.hash || "#/";
  // Unwatched until a view says so, so a view that throws cannot inherit the
  // previous page's refresh.
  view = null;
  pending = false;
  showNotice();
  forgetParts();
  leaveEveryRecord();
  // The page is being replaced anyway, so a pending reload costs nothing; it
  // lands on the new route.
  if (updateTo && (await reloadWhenSafe("leaving"))) return;
  for (const [pattern, handler] of routes) {
    const match = hash.match(pattern);
    if (match) {
      for (const link of document.querySelectorAll(".bar a")) {
        // Not toggleAttribute, which writes aria-current="": the stylesheet and
        // assistive tech want "page".
        if (link.getAttribute("href") === hash) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
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
