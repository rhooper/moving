// Moving boxes -- phone-first PWA. Hash routing so a scanned label can land on
// /#/b/CODE without needing server-side routes for every view.

const STATUSES = ["open", "packed", "loaded", "delivered", "unpacked"];
const app = document.getElementById("app");

// --- api ------------------------------------------------------------------

const keyStore = {
  get() {
    try { return localStorage.getItem("moving.key"); } catch { return null; }
  },
  set(value) {
    try { localStorage.setItem("moving.key", value); } catch { /* private mode */ }
  },
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["content-type"] = "application/json";
  const key = keyStore.get();
  if (key) headers["X-API-Key"] = key;

  const response = await fetch(`/api${path}`, { ...options, headers });

  if (response.status === 401) {
    const entered = prompt("This server needs an access key.");
    if (entered) {
      keyStore.set(entered);
      return api(path, options);
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

// --- helpers --------------------------------------------------------------

// Every value interpolated into markup goes through this. The rule is
// absolute: no template literal below inserts a raw value, including ids and
// counts, so there is no judgement call about which fields are "safe".
function escape(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function flagsOf(box) {
  return [
    box.fragile && "Fragile",
    box.open_first && "Open first",
    box.heavy && "Heavy",
  ].filter(Boolean);
}

function show(markup) { app.innerHTML = markup; }

function showError(message) {
  show(`<div class="err"><strong>${escape(message)}</strong></div>
        <p><a href="#/">Back to boxes</a></p>`);
}

// --- views ----------------------------------------------------------------

async function viewBoxes(query) {
  const boxes = await api(query ? `/search?q=${encodeURIComponent(query)}` : "/boxes?limit=100");

  const list = boxes.length
    ? `<ul class="boxlist">${boxes.map((b) => `
        <li><a href="#/b/${escape(b.code)}">
          <span class="c">${escape(b.code)}</span>
          <span class="s">${escape(b.content_summary || "Nothing written down yet")}</span>
          <span class="w">${escape(b.current_location || b.status)}</span>
        </a></li>`).join("")}</ul>`
    : query
      ? `<div class="empty"><p>Nothing matches “${escape(query)}”.</p></div>`
      : `<div class="empty">
           <p>No boxes yet.</p>
           <p><a href="#/new">Make the first one</a> and print its label.</p>
         </div>`;

  show(`
    <form id="search" class="row" role="search">
      <input name="q" type="search" placeholder="Find a box or something in one"
             value="${escape(query || "")}" aria-label="Search boxes">
      <button class="btn" type="submit">Search</button>
    </form>
    <div class="section">
      <h2>${query ? `Matches for “${escape(query)}”` : `${boxes.length} box${boxes.length === 1 ? "" : "es"}`}</h2>
      ${list}
    </div>`);

  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("q").trim();
    location.hash = value ? `#/search/${encodeURIComponent(value)}` : "#/";
  });
}

async function viewBox(code) {
  const [box, items, rooms] = await Promise.all([
    api(`/boxes/${encodeURIComponent(code)}`),
    api(`/boxes/${encodeURIComponent(code)}/items`),
    api("/rooms"),
  ]);
  const room = rooms.find((r) => r.id === box.destination_room_id);
  const flags = flagsOf(box);

  show(`
    <h1 class="code">${escape(box.code)}</h1>
    ${flags.length ? `<div class="flags">${flags.map((f) => `<span class="flag">${escape(f)}</span>`).join("")}</div>` : ""}
    ${room ? `<div class="band">${escape(room.name)}</div>` : ""}
    ${box.source_location ? `<p class="meta">From ${escape(box.source_location)}</p>` : ""}
    <p id="summary-text">${escape(box.content_summary || "Nothing written down yet.")}</p>

    <div class="section">
      <h2>Where it is</h2>
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
      </form>
    </div>

    <div class="section">
      <h2>What is in it</h2>
      <ul class="items">${items.map((i) => `
        <li>
          <span>${escape(i.name)}${i.source === "ai" ? ' <span class="ai">drafted</span>' : ""}</span>
          ${i.qty > 1 ? `<span class="qty">×${escape(i.qty)}</span>` : ""}
          <button data-remove="${escape(i.id)}" aria-label="Remove ${escape(i.name)}">Remove</button>
        </li>`).join("")}</ul>
      <form id="add-item" class="row" style="margin-top:0.75rem">
        <input name="name" placeholder="Add something" aria-label="Item name" required>
        <button class="btn" type="submit">Add</button>
      </form>
    </div>

    <div class="section">
      <h2>Label</h2>
      <p class="meta">Printed ${escape(box.label_print_count || 0)} time${box.label_print_count === 1 ? "" : "s"}.</p>
      <div class="row">
        <button class="btn quiet" id="print">Print label</button>
      </div>
    </div>`);

  for (const button of app.querySelectorAll("[data-status]")) {
    button.addEventListener("click", () => act(() =>
      api(`/boxes/${encodeURIComponent(code)}/status`, { method: "POST", body: JSON.stringify({ status: button.dataset.status }) })));
  }
  for (const button of app.querySelectorAll("[data-remove]")) {
    button.addEventListener("click", () => act(() =>
      api(`/items/${encodeURIComponent(button.dataset.remove)}`, { method: "DELETE" })));
  }
  document.getElementById("location").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = new FormData(event.target).get("current_location").trim();
    act(() => api(`/boxes/${encodeURIComponent(code)}/location`, {
      method: "POST", body: JSON.stringify({ current_location: value || null }) }));
  });
  document.getElementById("add-item").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name").trim();
    if (name) act(() => api(`/boxes/${encodeURIComponent(code)}/items`, { method: "POST", body: JSON.stringify({ name }) }));
  });
  document.getElementById("print").addEventListener("click", () => act(() =>
    api("/labels/print", { method: "POST", body: JSON.stringify({ codes: [code] }) })));

  async function act(operation) {
    try { await operation(); await viewBox(code); }
    catch (error) { showError(error.message); }
  }
}

async function viewNew() {
  const rooms = await api("/rooms");
  show(`
    <h1 class="code">New box</h1>
    <form id="new">
      <div class="section">
        <h2>Where it is going</h2>
        <select name="destination_room_id" aria-label="Destination room">
          <option value="">Not decided yet</option>
          ${rooms.map((r) => `<option value="${escape(r.id)}">${escape(r.name)}</option>`).join("")}
        </select>
      </div>
      <div class="section">
        <h2>What is in it</h2>
        <textarea name="content_summary" rows="3" placeholder="pots, baking pans, stand mixer"></textarea>
        <input name="source_location" placeholder="Where you packed it from" style="margin-top:0.5rem">
        <label style="display:flex;gap:0.6rem;align-items:center;margin-top:0.75rem">
          <input type="checkbox" name="fragile" style="width:auto;min-height:auto"> Fragile
        </label>
      </div>
      <div class="section">
        <button class="btn" type="submit">Create and print label</button>
      </div>
    </form>`);

  document.getElementById("new").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      content_summary: form.get("content_summary").trim() || null,
      source_location: form.get("source_location").trim() || null,
      fragile: form.get("fragile") === "on",
    };
    const roomId = form.get("destination_room_id");
    if (roomId) payload.destination_room_id = Number(roomId);

    try {
      const box = await api("/boxes", { method: "POST", body: JSON.stringify(payload) });
      await api("/labels/print", { method: "POST", body: JSON.stringify({ codes: [box.code] }) });
      location.hash = `#/b/${box.code}`;
    } catch (error) { showError(error.message); }
  });
}

// --- routing --------------------------------------------------------------

const routes = [
  [/^#?\/?$/, viewBoxes],
  [/^#\/search\/(.+)$/, (q) => viewBoxes(decodeURIComponent(q))],
  [/^#\/b\/([^/]+)$/, viewBox],
  [/^#\/new$/, viewNew],
  [/^#\/scan$/, () => import("/scan.js").then((m) => m.viewScan(show, showError))],
];

async function route() {
  const hash = location.hash || "#/";
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

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* http, or blocked */ });
}
