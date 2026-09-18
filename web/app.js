// Moving boxes -- phone-first PWA. Hash routing so a scanned label can land on
// /#/b/CODE without needing server-side routes for every view.

import { coverUrl, stripFor } from "/covers.js";
import {
  LiveChannel,
  SETTLE_MS,
  affects,
  hasUnsavedEdits,
  holdRefresh,
  isDirty,
  reconcile,
} from "/live.js";
import { splitItems } from "/text.js";

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
    try { detail = (await response.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
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

// Cancel for a form on a record that already exists. Hidden until something
// in the form differs from what was drawn, so an untouched page is not
// littered with buttons; pressing it puts every field back to what
// markPristine recorded. Nothing reaches the server either way -- these forms
// only save when you press their save button.
//
// Anything that sets a field's value from code (a suggestion, dictation) must
// dispatch an "input" event, or Cancel will not know there is something to
// cancel.
function wireCancel(form) {
  const cancel = form?.querySelector("[data-cancel]");
  if (!cancel) return;
  const fields = () => Array.from(form.querySelectorAll("input, textarea, select"));
  const sync = () => {
    cancel.hidden = !isDirty(
      fields().map((f) => ({ value: fieldValue(f), initial: f.dataset.initial })));
  };
  form.addEventListener("input", sync);
  form.addEventListener("change", sync);
  cancel.addEventListener("click", () => {
    for (const field of fields()) {
      const initial = field.dataset.initial ?? "";
      if (field.type === "checkbox") field.checked = initial === "true";
      else field.value = initial;
    }
    sync();
    announce("Changes cancelled. Nothing was saved.");
  });
  sync();
}

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
        <p><a href="#/">Back to boxes</a></p>`);
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

// --- views ----------------------------------------------------------------

const boxesPath = (query) =>
  query ? `/search?q=${encodeURIComponent(query)}` : "/boxes?limit=100";

const listHeading = (boxes, query) =>
  query ? `Matches for “${query}”` : `${boxes.length} box${boxes.length === 1 ? "" : "es"}`;

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
  row.append(link);
  return row;
}

function fillRow(row, box) {
  const [code, summary, where] = row.querySelectorAll("span.c, span.s, span.w");
  setText(code, box.code);
  setText(summary, box.content_summary || "Nothing written down yet");
  setText(where, box.current_location || box.status);
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
          <span class="w">${escape(b.kind && b.kind !== "box" ? b.kind : (b.current_location || b.status))}</span>
        </a></li>`).join("")}</ul>`
    : query
      ? `<div class="empty"><p>Nothing matches “${escape(query)}”.</p></div>`
      : `<div class="empty">
           <p>No boxes yet.</p>
           <p><a href="#/new">Make the first one.</a></p>
         </div>`;

  show(`
    <form id="search" class="row" role="search">
      <input name="q" type="search" placeholder="Find a box or something in one"
             value="${escape(query || "")}" aria-label="Search boxes">
      <button class="btn" type="submit">Search</button>
    </form>
    <div class="section">
      <h2 id="list-heading">${escape(listHeading(boxes, query))}</h2>
      ${list}
    </div>`);

  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("q").trim();
    location.hash = value ? `#/search/${encodeURIComponent(value)}` : "#/";
  });

  watch({ name: "list", query, refresh: (at) => refreshBoxes(query, at) });
}

async function viewBox(code, { keepBanner = false, at = null } = {}) {
  const held = keepBanner ? document.getElementById("say")?.outerHTML : null;
  const path = `/boxes/${encodeURIComponent(code)}`;
  const [box, items, rooms, photos, press, allKinds] = await Promise.all([
    api(path),
    api(`${path}/items`),
    api("/rooms"),
    api(`${path}/photos`),
    api("/printer").catch(() => null),
    api("/settings/kinds"),
  ]);
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
    ${room ? `<div class="band">${escape(room.name)}</div>` : ""}
    <form id="summary-form">
      <label class="dlabel" for="what">${shape.contents ? "What is in it" : "What it is"}</label>
      <textarea id="what" name="content_summary" rows="2"
        placeholder="${shape.contents ? "pots, baking pans, stand mixer" : "Bicycle (Trek hybrid, blue)"}"
        >${escape(box.content_summary || "")}</textarea>
      <div class="row" style="margin-top:0.5rem">
        <button class="btn quiet" type="submit">Save ${shape.contents ? "summary" : "name"}</button>
        <button class="btn quiet" type="button" data-cancel hidden>Cancel</button>
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
        <div class="row" style="margin-top:0.75rem">
          <button class="btn quiet" type="submit">Save</button>
          <button class="btn quiet" type="button" data-cancel hidden>Cancel</button>
        </div>
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
      <form id="location" class="row" style="margin-top:0.75rem">
        <input name="current_location" placeholder="Truck, garage stack 3, storage…"
               value="${escape(box.current_location || "")}" aria-label="Current location">
        <button class="btn" type="submit">Move</button>
        <button class="btn quiet" type="button" data-cancel hidden>Cancel</button>
      </form>
    </div>

    ${!shape.contents ? "" : `
    <div class="section">
      <h2>What is in it</h2>
      <ul class="items">${items.map((i) => `
        <li>
          <span>${escape(i.name)}${i.source === "ai" ? ' <span class="ai">autogenerated</span>' : ""}</span>
          ${i.qty > 1 ? `<span class="qty">×${escape(i.qty)}</span>` : ""}
          <button data-remove="${escape(i.id)}" aria-label="Remove ${escape(i.name)}">Remove</button>
        </li>`).join("")}</ul>
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
         record of what went in.</p>
      ${photos.length ? `<p class="meta">The cover is the picture this box is
         shown by in the list.</p>` : ""}
      <div class="shots">${stripFor(photos).map((p) => `
        <figure${p.cover ? ' class="is-cover"' : ""}>
          <a href="/photos/${escape(p.id)}/full" target="_blank" rel="noreferrer">
            <img src="${escape(p.thumb)}" alt="${escape(p.caption || "Box contents")}"
                 width="${escape(p.width)}" height="${escape(p.height)}" loading="lazy">
          </a>
          <div class="acts">
            ${p.cover
              ? '<span class="mark">Cover</span>'
              : `<button data-cover="${escape(p.id)}"
                    aria-label="Use this photo as the cover">Make cover</button>`}
            <button data-drop-photo="${escape(p.id)}" aria-label="Delete this photo">Delete</button>
          </div>
        </figure>`).join("")}</div>
      <div class="row" style="margin-top:0.75rem">
        <label class="btn" for="shot">Take a photo
          <input id="shot" type="file" accept="image/*" capture="environment" hidden>
        </label>
        ${photos.length ? '<button class="btn quiet" id="draft-btn">Draft contents</button>' : ""}
      </div>
      <div id="draft-panel"></div>
    </div>

    <div class="section">
      <h2>Label</h2>
      <p class="meta">Printed ${escape(box.label_print_count || 0)} time${box.label_print_count === 1 ? "" : "s"}.</p>
      ${printerLine(press)}
      <div class="row">
        <button class="btn quiet" id="print">Print label</button>
      </div>
      ${hasContents(box, items) ? "" : `
        <label class="anyway">
          <input type="checkbox" id="print-anyway">
          Print anyway - nothing is recorded in this box yet
        </label>`}
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
  for (const button of app.querySelectorAll("[data-remove]")) {
    button.addEventListener("click", () => act(() =>
      api(`/items/${encodeURIComponent(button.dataset.remove)}`, { method: "DELETE" })));
  }
  // What it is / what is in it. Saved explicitly: the field is also where a
  // suggestion or a draft lands, and those are offered, never applied.
  const summaryForm = document.getElementById("summary-form");
  for (const id of ["summary-form", "destination", "location"]) {
    wireCancel(document.getElementById(id));
  }
  summaryForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = new FormData(summaryForm).get("content_summary").trim();
    act(() => api(path, {
      method: "PATCH", body: JSON.stringify({ content_summary: value || null }) }));
  });

  // Kind, where it is going, where it came from. An unset room is null, not
  // the empty string a <select> reports.
  const destinationForm = document.getElementById("destination");
  destinationForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(destinationForm);
    const roomId = (name) => (form.get(name) ? Number(form.get(name)) : null);
    act(() => api(path, {
      method: "PATCH",
      body: JSON.stringify({
        kind: form.get("kind"),
        destination_room_id: roomId("destination_room_id"),
        source_room_id: roomId("source_room_id"),
        source_location: form.get("source_location").trim() || null,
      }),
    }));
  });

  document.getElementById("location").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("current_location").trim();
    act(() => api(`/boxes/${encodeURIComponent(code)}/location`, {
      method: "POST", body: JSON.stringify({ current_location: value || null }) }));
  });
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
      await viewBox(code);
    } catch (error) { failed(error.message); }
  });
  const suggest = document.getElementById("suggest");
  suggest?.addEventListener("click", async () => {
    try {
      const { summary } = await busy(suggest, "Reading…", () =>
        api(`${path}/summary-suggestion`));
      const field = summaryForm.querySelector("[name=content_summary]");
      if (!summary) {
        announce("Nothing to summarise yet - add some items first.", { warn: true });
        return;
      }
      // Offered, not applied: it lands in the field and you press Save -- or
      // Cancel, which appears because the field now differs from what was drawn.
      field.value = summary;
      edited(field);
      field.focus();
      announce("Summary suggested from the contents. Save it, or Cancel to put the old one back.");
    } catch (error) { failed(error.message); }
  });

  // Spell out what is about to be destroyed. "Are you sure?" tells you
  // nothing; the count of photos and items, and whether a label for this code
  // is already stuck to something, are what actually inform the decision.
  const losing = [
    items.length && `${items.length} item${items.length === 1 ? "" : "s"}`,
    photos.length && `${photos.length} photo${photos.length === 1 ? "" : "s"}`,
  ].filter(Boolean);
  const printed = box.label_print_count || 0;

  const note = document.getElementById("delete-note");
  if (note) {
    // Reversible, so the note describes what goes out of view rather than
    // warning about loss. Nothing here is destroyed.
    note.textContent = [
      "Takes it out of the list and out of search. Nothing is destroyed, and you can restore it.",
      losing.length ? `Its ${losing.join(" and ")} go with it.` : "",
    ].filter(Boolean).join(" ");
  }

  const deleteButton = document.getElementById("delete");
  deleteButton?.addEventListener("click", async () => {
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
    const detail = [
      losing.length ? `Destroys its ${losing.join(" and ")}, including the photo files.` : "",
      printed ? `A label has been printed ${printed} time${printed === 1 ? "" : "s"} - if one is on something it will scan to nothing.` : "",
      `${code} will not be reused.`,
    ].filter(Boolean).join(" ");
    if (!confirm(`Permanently delete ${code}?\n\n${detail}\n\nThis cannot be undone.`)) return;
    try {
      await busy(event.target, "Deleting…", () =>
        api(`${path}/purge`, { method: "DELETE" }));
      location.hash = "#/";
    } catch (error) { failed(error.message); }
  });

  const printButton = document.getElementById("print");
  printButton.addEventListener("click", async () => {
    const anyway = document.getElementById("print-anyway");
    let result;
    try {
      result = await busy(printButton, "Printing…", () =>
        api("/labels/print", {
          method: "POST",
          body: JSON.stringify({ codes: [code], allow_empty: Boolean(anyway?.checked) }),
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
        announce(`Printed ${code}.`);
      }
      await viewBox(code, { keepBanner: true });
    } catch (error) { failed(error.message); }
  });

  document.getElementById("shot").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file, file.name || "photo.jpg");
    const panel = document.getElementById("draft-panel");
    panel.innerHTML = `<p class="meta">Uploading ${escape(file.name || "photo")}…</p>`;
    event.target.disabled = true;
    try {
      // No content-type header: the browser must set the multipart boundary.
      await api(`${path}/photos`, { method: "POST", body });
      await viewBox(code);
    } catch (error) { failed(error.message); }
    finally { event.target.disabled = false; }
  });

  for (const button of app.querySelectorAll("[data-drop-photo]")) {
    button.addEventListener("click", () => act(() =>
      request(`/photos/${encodeURIComponent(button.dataset.dropPhoto)}`, { method: "DELETE" })));
  }

  // Not under /api: photo files and their controls sit at the root, so this
  // goes through request() rather than api().
  for (const button of app.querySelectorAll("[data-cover]")) {
    button.addEventListener("click", () => act(() =>
      request(`/photos/${encodeURIComponent(button.dataset.cover)}/cover`, { method: "POST" })));
  }

  const draftButton = document.getElementById("draft-btn");
  if (draftButton) {
    draftButton.addEventListener("click", async () => {
      const panel = document.getElementById("draft-panel");
      panel.innerHTML = `<p class="meta">Reading the photo with the vision model.
        A few seconds, longer the first time while it loads.</p>`;
      try {
        const { draft } = await busy(draftButton, "Reading…", () =>
          api(`${path}/ai/draft`, { method: "POST", body: "{}" }));
        renderDraft(panel, draft);
      } catch (error) {
        panel.innerHTML = `<div class="err"><strong>${escape(error.message)}</strong></div>`;
      }
    });
  }

  function renderDraft(panel, draft) {
    // Nothing is applied until this is accepted. The model proposes; you decide.
    panel.innerHTML = `
      <div class="draft">
        <h3>Suggested contents</h3>
        <p class="meta">Nothing is saved until you accept. Untick anything wrong.</p>
        <label class="dlabel" for="d-summary">Summary</label>
        <textarea id="d-summary" rows="2">${escape(draft.summary || "")}</textarea>
        <ul class="items">${draft.items.map((item, index) => `
          <li>
            <input type="checkbox" id="d-${index}" checked style="width:auto;min-height:auto">
            <label for="d-${index}" style="flex:1">${escape(item.name)}${item.qty > 1 ? ` ×${escape(item.qty)}` : ""}</label>
          </li>`).join("")}</ul>
        ${draft.fragile ? '<p><span class="flag">Fragile</span> suggested</p>' : ""}
        <div class="row" style="margin-top:0.75rem">
          <button class="btn" id="d-accept">Accept</button>
          <button class="btn quiet" id="d-discard">Discard</button>
        </div>
      </div>`;

    document.getElementById("d-discard").addEventListener("click", () => {
      panel.innerHTML = "";
    });

    const accept = document.getElementById("d-accept");
    accept.addEventListener("click", async () => {
      const summary = document.getElementById("d-summary").value.trim();
      const chosen = draft.items.filter((_, i) => document.getElementById(`d-${i}`).checked);
      try {
        const patch = {};
        if (summary) patch.content_summary = summary;
        if (draft.fragile) patch.fragile = true;
        if (Object.keys(patch).length) {
          await api(path, { method: "PATCH", body: JSON.stringify(patch) });
        }
        await busy(accept, "Saving…", async () => {
          for (const item of chosen) {
            // source: "ai" keeps drafted items distinguishable from typed ones.
            await api(`${path}/items`, {
              method: "POST",
              body: JSON.stringify({ name: item.name, qty: item.qty, source: "ai" }),
            });
          }
        });
        await viewBox(code);
      } catch (error) { failed(error.message); }
    });
  }

  async function act(operation) {
    try { await operation(); await viewBox(code); }
    catch (error) { failed(error.message); }
  }

  // Last, so a half-built page is never the thing a refresh redraws.
  watch({ name: "box", code, refresh: (at) => viewBox(code, { keepBanner: true, at }) });
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
        <button class="btn" type="submit" id="create">Create</button>
        <button class="btn quiet" type="submit" id="create-print" data-print
                style="margin-top:0.5rem">Create and print label</button>
      </div>
    </form>`);

  // Creating is the default -- first button, and what Enter does. Printing is
  // the deliberate second choice: tape is the one thing here that cannot be
  // undone. The first button names what it makes, following the kind picker.
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

    // Which button was pressed. Enter in a field reports the first submit
    // button, so the keyboard default is create-without-printing too.
    const pressed = event.submitter || document.getElementById("create");
    const wantsLabel = pressed.hasAttribute("data-print");

    try {
      let unprinted = null;
      const box = await busy(pressed, wantsLabel ? "Creating and printing…" : "Creating…", async () => {
        const made = await api("/boxes", { method: "POST", body: JSON.stringify(payload) });
        if (wantsLabel) {
          try {
            await api("/labels/print", { method: "POST", body: JSON.stringify({ codes: [made.code] }) });
          } catch (error) { unprinted = error.message; }
        }
        return made;
      });
      location.hash = `#/b/${box.code}`;
      if (unprinted) {
        failed(`${box.code} was created, but its label did not print: ${unprinted}`,
               "Label not printed");
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
// it can take the keyboard away mid-word, throw away an unsaved edit, discard
// an AI draft nobody accepted yet, or -- worst -- move a row out from under a
// thumb that is already coming down on it. So every refresh asks permission
// first, and when the answer is no it waits and says so instead.

// The view currently on screen, and how to bring it up to date. Views that
// are not in this list -- the new-box form, the scanner -- are never
// refreshed at all: `affects` matches nothing for them.
let view = null;
let pending = false;
let flushTimer = null;

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
}

function holdState() {
  const draft = document.getElementById("draft-panel");
  return {
    pointerDown,
    lastTouch,
    editing: hasUnsavedEdits(editableFields()),
    // A draft exists only in the DOM until somebody accepts it. A redraw
    // would throw away a proposal that cost ten seconds of a vision model.
    drafting: Boolean(draft && draft.firstElementChild),
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

function scheduleFlush() {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, SETTLE_MS);
}

function flush() {
  flushTimer = null;
  if (!pending || !view) return;
  const hold = holdState();
  // Still mid-edit or mid-draft: the notice stays up and the person decides.
  // Waking this again is the job of the focusout/change listeners below, not
  // of a timer that would otherwise tick forever behind a focused field.
  if (hold.editing || hold.drafting) return;
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
  onEvent: (event) => { if (affects(event, view)) requestRefresh(); },
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
      <p class="meta">If the printer powers itself off, turn that off once in
         Brother's Printer Setting Tool: Device Settings &gt; Basic &gt;
         Auto Power Off &gt; None. It cannot be set over USB.</p>
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
