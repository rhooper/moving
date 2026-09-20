#!/usr/bin/env node
// Purpose: two things about a record inside a container, pressed for real.
//          It goes where the container goes: no destination row of its own,
//          the band and a line saying whose room it is, the row back in place
//          when it is taken out. And fragile climbs: marking it fragile offers
//          to mark the containers too (accepted here, and seen on the
//          container), clearing it touches nothing, putting a fragile thing
//          inside something offers again, and so does creating one inside with
//          Fragile ticked. Uses the page's own dialog, clicked, not stubbed.
//          And adding something inside from the container's page: the dialog
//          asks for a kind, a photo and a source, Add makes it and the row
//          appears without leaving the page, Add and open lands on it, Cancel
//          makes nothing, and a photo that fails to upload (failed at the
//          network) leaves the record standing with a visible message.
//          And tapping one of those rows: it opens that record's editor over
//          the container as a modal, folds away the sections it has nothing
//          in, saves itself, keeps its own undo stack while the page behind
//          keeps the container's, commits what is pending when it closes, and
//          takes a change from elsewhere in place.
// Date:    2026-09-20
// Usage:   node scripts/claude/nesting_check.mjs <base-url>
//          WRITES: creates records and marks them. Refuses the live service.
//          scripts/claude/browser_checks.sh runs it on a throwaway.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { crc32, deflateSync } from "node:zlib";

// A real PNG, made here so the check needs no image file and no Pillow.
function png(size, [r, g, b]) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0);
    body.copy(out, 4);
    out.writeUInt32BE(crc32(body), body.length + 4);
    return out;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4);
  header[8] = 8; header[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 3).map((_, i) => [r, g, b][i % 3])]);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(Buffer.concat(Array(size).fill(row)))), chunk("IEND", Buffer.alloc(0)),
  ]);
}

const NAME = "nesting_check";
// This check creates records and marks them fragile. Never against the live
// service: the real database, with labels in circulation.
{
  const target = new URL(process.argv[2] || "http://127.0.0.1:0");
  if ((target.port || "80") === "8787" || !["127.0.0.1", "localhost"].includes(target.hostname)) {
    console.error(`${NAME}: refusing ${target.host}. This check writes. Run it against a ` +
      "throwaway server -- scripts/claude/browser_checks.sh starts one.");
    process.exit(2);
  }
}

const base = process.argv[2];
// Overridable: this is not the only headless Chrome on the machine.
const PORT = Number(process.env.CDP_PORT) || 9353;
const profile = mkdtempSync(join(tmpdir(), "nesting-check-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let wsUrl;
for (let i = 0; i < 150 && !wsUrl; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((p) => p.type === "page")?.webSocketDebuggerUrl; } catch {}
  await sleep(100);
}
const ws = new WebSocket(wsUrl); await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map(); const thrown = [];
let failing = null;   // { matches(request), left }: requests to fail at the network
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails.exception?.description || "?");
  if (msg.method === "Fetch.requestPaused") {
    const doomed = failing && failing.left > 0 && failing.matches(msg.params.request);
    if (doomed) failing.left -= 1;
    send(doomed ? "Fetch.failRequest" : "Fetch.continueRequest",
         doomed ? { requestId: msg.params.requestId, errorReason: "ConnectionRefused" } : { requestId: msg.params.requestId });
  }
  pending.get(msg.id)?.(msg); pending.delete(msg.id);
};
const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description);
  return r.result.result.value;
};
const waitFor = async (expression, what, tries = 100) => {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return; await sleep(100); }
  throw new Error(`timed out: ${what}`);
};
const goto = async (hash, ready) => { await evaluate(`location.hash = ${JSON.stringify(hash)}`); await sleep(300); await waitFor(ready, `${hash} to draw`); await sleep(200); };
const api = async (path, method = "GET", body) =>
  (await fetch(`${base}/api${path}`, {
    method, headers: { "content-type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) })).json();
const q = (selector) => `document.querySelector(${JSON.stringify(selector)})`;
// A real click on an element's centre: the dialog's buttons take a real press.
const click = async (selector) => {
  const spot = await evaluate(`(() => { const el = ${q(selector)}; if (!el) return null; el.scrollIntoView({ block: "center" });
    const b = el.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, seen: b.width > 0 && b.height > 0 }; })()`);
  if (!spot) throw new Error(`nothing to click: ${selector}`);
  // Folded away or otherwise unrendered: clicking its 0x0 box would land at
  // the top-left of the window, which inside a dialog is the backdrop.
  if (!spot.seen) throw new Error(`not visible to click: ${selector}`);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send("Input.dispatchMouseEvent", { type, x: spot.x, y: spot.y, button: "left", clickCount: 1 });
  }
  await sleep(80);
};

// Real keystrokes: a pause in typing is what makes a text field save.
const type = async (text, gap = 15) => {
  for (const ch of text) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
    await sleep(gap);
  }
};
const press = async (key, code = key, vk = 0, text = "") => {
  await send("Input.dispatchKeyEvent",
    { type: text ? "keyDown" : "rawKeyDown", key, code, windowsVirtualKeyCode: vk, text });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: vk });
  await sleep(60);
};

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);
const dialog = () => evaluate(`(() => { const d = document.querySelector("dialog.ask[open]"); if (!d) return null;
  return { title: d.querySelector("h2").textContent, text: d.querySelector("p").textContent,
           yes: d.querySelector("[value=yes]").textContent, no: d.querySelector("[value=no]").textContent,
           focused: document.activeElement?.value }; })()`);
const band = () => evaluate(`(() => { const b = ${q("#room-band")}; return b.hidden ? null : b.textContent; })()`);
const roomRow = () => evaluate(`(() => { const r = ${q('.seg[data-name="destination_room_id"]')}; return r ? { hidden: r.hidden, disabled: r.disabled } : null; })()`);
const goesWith = () => evaluate(`(() => { const l = ${q("#goes-with")}; return l && !l.hidden ? l.textContent : null; })()`);
const fragileOf = async (code) => (await api(`/boxes/${code}`)).fragile;
// Measured rather than read off the stylesheet: a tap target is a rendered
// box, and --tap is a token two rules have to agree about.
const reach = (selector) => evaluate(`(() => { const el = ${q(selector)}; if (!el) return null;
  const b = el.getBoundingClientRect();
  return { h: Math.round(b.height), size: Math.round(parseFloat(getComputedStyle(el).fontSize)) }; })()`);

try {
  const rooms = await api("/rooms");
  const kitchen = rooms.find((r) => r.name === "Kitchen") || rooms.find((r) => r.kind !== "source");
  const crate = (await api("/boxes", "POST", { kind: "crate", content_summary: "kitchen", destination_room_id: kitchen.id })).code;
  const box = (await api("/boxes", "POST", { kind: "box", content_summary: "glasses", parent_code: crate })).code;
  const bag = (await api("/boxes", "POST", { kind: "bag", content_summary: "stemware", parent_code: box })).code;

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Page.navigate", { url: `${base}/#/b/${bag}` });
  await waitFor(`Boolean(${q("#trail")}) && !${q("#trail")}.hidden`, "the nested record");
  await sleep(300);

  // --- it goes where its container goes ---
  check("nested: no destination row is offered", JSON.stringify(await roomRow()) === JSON.stringify({ hidden: true, disabled: true }), JSON.stringify(await roomRow()));
  check("nested: the line says whose room it is, two levels up, and which room",
        (await goesWith()) === `Goes where ${crate} goes: ${kitchen.name}.`, await goesWith());
  check("nested: the band shows the inherited room", (await band()) === kitchen.name, await band());
  check("nested: the line links to that container", (await evaluate(`${q("#goes-with a")}?.getAttribute("href")`)) === `#/b/${crate}`);

  // The way out of a nested record: the link a thumb reaches for with the
  // other hand holding the box. Both places it appears have to be a full tap
  // tall and set at the body size, not as a caption.
  const crumb = await reach("#trail a");
  check("nested: the breadcrumb out is a tap-sized target, at the body size",
        crumb && crumb.h >= 48 && crumb.size >= 16, JSON.stringify(crumb));
  const wayOut = await reach("#inside-of a");
  check("nested: so is the link in 'What it is inside'",
        wayOut && wayOut.h >= 48 && wayOut.size >= 16, JSON.stringify(wayOut));
  const lineInside = await reach("#inside-of");

  // Take it out: its own row comes back, in place, and the band empties.
  await evaluate(`${q("#summary-form [name=content_summary]")}.__mark = "kept"`);
  await click("#container-take");
  await waitFor(`${q("#container .autosave-state")}.textContent === "Saved" && !${q('.seg[data-name="destination_room_id"]')}.hidden`, "taking it out");
  check("taken out: the destination row is back, enabled, without a redraw",
        JSON.stringify(await roomRow()) === JSON.stringify({ hidden: false, disabled: false })
          && (await evaluate(`${q("#summary-form [name=content_summary]")}.__mark`)) === "kept", JSON.stringify(await roomRow()));
  check("taken out: no inherited room, no band, no line", (await band()) === null && (await goesWith()) === null);
  // The line is redrawn in place when a record is moved, so it must be the
  // same height with the link gone -- otherwise the section jumps under the
  // finger that just pressed Take it out.
  const lineLoose = await reach("#inside-of");
  check("taken out: the line keeps its height, so nothing below it moves",
        lineInside && lineLoose && lineInside.h === lineLoose.h, `${JSON.stringify(lineInside)} vs ${JSON.stringify(lineLoose)}`);
  await click("#container .undo");
  await waitFor(`${q('.seg[data-name="destination_room_id"]')}.hidden`, "undoing that");
  check("put back by Undo: the row goes away again and the room returns", (await band()) === kitchen.name && (await goesWith()) !== null);

  // --- fragile climbs ---
  check("(setup) nothing is fragile yet", (await fragileOf(bag)) === 0 && (await fragileOf(box)) === 0 && (await fragileOf(crate)) === 0);
  await click("[data-flag=fragile]");
  await waitFor(`Boolean(document.querySelector("dialog.ask[open]"))`, "the climb to be offered");
  let asked = await dialog();
  check("marking it fragile asks about the containers that are not",
        asked && asked.text.includes(box) && asked.text.includes(crate) && /not marked fragile/.test(asked.text), JSON.stringify(asked));
  check("with the safe answer focused and worded", asked?.focused === "no" && asked?.no === "Not now" && asked?.yes === "Mark them fragile", JSON.stringify(asked));
  await click("dialog.ask [value=yes]");
  await waitFor(`!document.querySelector("dialog.ask[open]") && ${q("[data-flag=fragile]")}?.classList.contains("on")`, "the marks and the redraw");
  await sleep(300);
  check("saying yes marks the record and both containers", (await fragileOf(bag)) === 1 && (await fragileOf(box)) === 1 && (await fragileOf(crate)) === 1,
        `${await fragileOf(bag)} ${await fragileOf(box)} ${await fragileOf(crate)}`);
  check("and the containers' pages would show it",
        (await api(`/boxes/${bag}`)).path.every((step) => step.fragile === 1));

  await click("[data-flag=fragile]");   // off again
  await waitFor(`!${q("[data-flag=fragile]")}?.classList.contains("on")`, "the record to be unmarked");
  await sleep(400);
  check("clearing it asks nothing", (await dialog()) === null);
  check("and the containers stay fragile", (await fragileOf(box)) === 1 && (await fragileOf(crate)) === 1 && (await fragileOf(bag)) === 0);

  // Marking it again: the containers are already fragile, so nothing to ask.
  await click("[data-flag=fragile]");
  await waitFor(`${q("[data-flag=fragile]")}?.classList.contains("on")`, "marking it again");
  await sleep(400);
  check("marking it again, with the containers already fragile, asks nothing", (await dialog()) === null);

  // Putting a fragile thing inside something that is not: offered on landing.
  const tub = (await api("/boxes", "POST", { kind: "tub", content_summary: "hallway" })).code;
  await evaluate(`${q("#container-code")}.value = ${JSON.stringify(tub)}; ${q("#container")}.requestSubmit()`);
  await waitFor(`!${q("#container-acts")}.hidden`, "the look-up of the tub");
  await click("#container-put");
  await waitFor(`Boolean(document.querySelector("dialog.ask[open]"))`, "the climb to be offered on the move");
  asked = await dialog();
  check("putting a fragile thing inside a container that is not asks about it",
        asked && asked.text.includes(tub) && !asked.text.includes(crate), JSON.stringify(asked));
  await click("dialog.ask [value=no]");
  await waitFor(`!document.querySelector("dialog.ask[open]")`, "Not now");
  await sleep(300);
  check("Not now leaves the container alone, and the move stands", (await fragileOf(tub)) === 0 && (await api(`/boxes/${bag}`)).parent?.code === tub);
  check("nothing threw so far", thrown.length === 0, thrown.join(" | "));

  // --- the new-record form inside a container ---
  await goto(`#/new/in/${tub}`, `Boolean(${q("#inside-note")})`);
  check("new form inside a container: no destination row", !(await evaluate(`Boolean(${q('#new .seg[data-name="destination_room_id"]')})`)));
  await evaluate(`${q("#new [name=fragile]")}.click(); ${q("#new [name=content_summary]")}.value = "wine glasses"`);
  await click("#create");
  await waitFor(`Boolean(document.querySelector("dialog.ask[open]"))`, "the climb to be offered after creating");
  asked = await dialog();
  check("created inside with Fragile ticked: it asks about the container", asked && asked.text.includes(tub), JSON.stringify(asked));
  await click("dialog.ask [value=yes]");
  await waitFor(`location.hash.startsWith("#/b/") && Boolean(${q("#trail")}) && !${q("#trail")}.hidden`, "landing on the new record");
  const made = (await evaluate("location.hash")).slice(4);
  check("saying yes marks the container, and then you are on the new record", (await fragileOf(tub)) === 1 && (await fragileOf(made)) === 1 && made !== tub,
        `${made}: ${await fragileOf(made)}, tub ${await fragileOf(tub)}`);

  // --- adding something inside, from the container's page ---
  const photo = join(profile, "shot.png");
  writeFileSync(photo, png(96, [180, 150, 110]));
  const source = rooms.find((r) => r.kind !== "destination");
  await goto(`#/b/${crate}`, `Boolean(${q("#add-inside")})`);
  const insideBefore = (await api(`/boxes/${crate}`)).children.length;
  await evaluate(`${q("#summary-form [name=content_summary]")}.__mark = "stayed"`);
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the add-inside dialog");
  check("Add something inside opens a dialog, not another page",
        (await evaluate("location.hash")) === `#/b/${crate}` && (await evaluate(`document.activeElement?.value`)) === "no");
  check("it asks for a kind, a photo and a source, and nothing else",
        await evaluate(`(() => { const d = document.querySelector("dialog.adder");
          return d.querySelector('.seg[data-name="kind"]') && d.querySelector('#adder-shot[type=file][capture]')
            && d.querySelector('.seg[data-name="source_room_id"]') && !d.querySelector("textarea")
            && !d.querySelector('.seg[data-name="destination_room_id"]') && !d.querySelector('.seg[data-name="size"]'); })()`));
  check("every kind is offered, single things included",
        (await evaluate(`[...document.querySelectorAll('dialog.adder .seg[data-name="kind"] input')].map((r) => r.value).join()`)) === "box,tub,crate,bag,item,furniture");
  await click(`dialog.adder .seg[data-name="kind"] input[value="bag"] + span`);
  await click(`dialog.adder .seg[data-name="source_room_id"] input[value="${source.id}"] + span`);
  const doc = await send("DOM.getDocument");
  const shotNode = await send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "dialog.adder #adder-shot" });
  await send("DOM.setFileInputFiles", { nodeId: shotNode.result.nodeId, files: [photo] });
  check("choosing a photo is acknowledged", /Photo: /.test(await evaluate(`${q("#adder-photo")}.textContent`)));
  await click("#adder-add");
  await waitFor(`!document.querySelector("dialog.adder")`, "Add to close the dialog", 150);
  const added = (await api(`/boxes/${crate}`)).children.at(-1);
  check("Add makes it inside, with the source, and stays on the container",
        (await api(`/boxes/${crate}`)).children.length === insideBefore + 1 && added.kind === "bag" && added.source_room_id === source.id
          && (await evaluate("location.hash")) === `#/b/${crate}`, JSON.stringify(added));
  // The row arrives by the in-place refetch, which waits out the click first.
  await waitFor(q(`#inside li[data-key="${added.code}"]`), "the new row to appear inside");
  check("the new row appears in place, the page not redrawn",
        (await evaluate(`${q("#summary-form [name=content_summary]")}.__mark`)) === "stayed");
  check("the line says what was added, with a link to open it",
        (await evaluate(`${q("#inside-said")}.textContent`)) === `Added ${added.code} (bag). Open it`
          && (await evaluate(`${q("#inside-said a")}.getAttribute("href")`)) === `#/b/${added.code}`, await evaluate(`${q("#inside-said")}.textContent`));
  const photos = await api(`/boxes/${added.code}/photos`);
  check("the photo went to the new record and is being read", photos.length === 1 && Boolean(photos[0].analysis), JSON.stringify(photos.map((p) => p.analysis?.status)));

  // Cancel makes nothing.
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the dialog again");
  await click("dialog.adder [value=no]");
  await waitFor(`!document.querySelector("dialog.adder")`, "Cancel");
  check("Cancel makes nothing", (await api(`/boxes/${crate}`)).children.length === insideBefore + 1);

  // Add and open lands on the new record.
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the dialog a third time");
  await click(`dialog.adder .seg[data-name="kind"] input[value="item"] + span`);
  await click("#adder-open");
  await waitFor(`location.hash !== ${JSON.stringify(`#/b/${crate}`)} && Boolean(${q("#trail")}) && !${q("#trail")}.hidden`, "landing on the new record");
  const opened = (await evaluate("location.hash")).slice(4);
  check("Add and open lands on the new record, inside the container", (await api(`/boxes/${opened}`)).parent?.code === crate && (await api(`/boxes/${opened}`)).kind === "item");

  // A photo that fails to upload: the record stands, and the dialog says so.
  await goto(`#/b/${crate}`, `Boolean(${q("#add-inside")})`);
  await send("Fetch.enable", { patterns: [{ urlPattern: `${base}/api/boxes/*/photos`, requestStage: "Request" }] });
  failing = { matches: (request) => request.method === "POST", left: 1 };
  const before = (await api(`/boxes/${crate}`)).children.length;
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the dialog a fourth time");
  const doc2 = await send("DOM.getDocument");
  const shot2 = await send("DOM.querySelector", { nodeId: doc2.result.root.nodeId, selector: "dialog.adder #adder-shot" });
  await send("DOM.setFileInputFiles", { nodeId: shot2.result.nodeId, files: [photo] });
  await click("#adder-add");
  await waitFor(`!${q("#adder-said")}.hidden`, "the dialog to say the photo failed");
  const complaint = await evaluate(`${q("#adder-said")}.textContent`);
  const stood = (await api(`/boxes/${crate}`)).children.at(-1);
  check("a failed photo upload leaves the record standing and the dialog open, saying so",
        (await api(`/boxes/${crate}`)).children.length === before + 1 && /did not upload/.test(complaint) && complaint.includes(stood.code)
          && (await evaluate(`Boolean(document.querySelector("dialog.adder[open]"))`)), complaint);
  check("the buttons now offer the photo again, or to open it",
        (await evaluate(`${q("#adder-add")}.textContent`)) === "Try the photo again" && (await evaluate(`${q("#adder-open")}.textContent`)) === "Open it");
  check("and the line under the section says so too", /did not upload/.test(await evaluate(`${q("#inside-said")}.textContent`)));
  await click("#adder-add");   // the network is back: the photo goes this time
  await waitFor(`!document.querySelector("dialog.adder")`, "the retried photo to land and the dialog to close");
  check("trying the photo again uploads it to the same record, no second record",
        (await api(`/boxes/${stood.code}/photos`)).length === 1 && (await api(`/boxes/${crate}`)).children.length === before + 1);
  await send("Fetch.disable");

  // --- editing something inside, over the container's page ---
  const bare = (await api("/boxes", "POST", { kind: "bag", parent_code: crate })).code;
  const full = (await api("/boxes", "POST", {
    kind: "box", content_summary: "tea things", size: "small", fragile: true, parent_code: crate })).code;
  await api(`/boxes/${full}/items`, "POST", { name: "teapot" });

  const editor = () => evaluate(`(() => { const d = document.querySelector("dialog.editor"); if (!d) return null;
    const undo = d.querySelector(".undo");
    return { open: d.open, code: d.querySelector("h2").textContent, where: d.querySelector("#child-where").textContent,
             folds: Object.fromEntries([...d.querySelectorAll("details.fold")].map((f) => [f.dataset.fold, f.open])),
             marks: [...d.querySelectorAll("details.fold")].map((f) => getComputedStyle(f.querySelector("summary"), "::before").content),
             line: d.querySelector(".autosave-state").textContent,
             undo: undo.hidden ? null : undo.textContent,
             summary: d.querySelector("#child-what")?.value, kind: d.querySelector('[name="kind"]:checked')?.value,
             mark: d.__mark }; })()`);
  const rowSays = (code) => evaluate(`${q("#inside li[data-key='" + code + "'] .s")}?.textContent`);
  const unfold = async (key) => {
    if (await evaluate(`document.querySelector('dialog.editor [data-fold="${key}"]').open`)) return;
    await click(`dialog.editor [data-fold="${key}"] > summary`);
    await sleep(150);
  };
  const openEditorOn = async (code) => {
    await click(`#inside li[data-key="${code}"] a`);
    await waitFor(`Boolean(document.querySelector("dialog.editor[open]"))`, `the editor on ${code}`);
    await sleep(250);
    await evaluate(`document.querySelector("dialog.editor").__mark = "same"`);
  };

  await goto(`#/b/${crate}`, `Boolean(${q(`#inside li[data-key="${bare}"]`)})`);
  const onContainer = await evaluate("location.hash");
  await openEditorOn(bare);
  let shown = await editor();
  check("tapping something inside opens its editor over the container, not another page",
        shown.open && shown.code === bare && (await evaluate("location.hash")) === onContainer, JSON.stringify(shown));
  check("it says what the record is and where it is going", /^A bag inside/.test(shown.where) && /going where/.test(shown.where), shown.where);
  check("an empty record folds every section away but the one it always has",
        JSON.stringify(shown.folds) === JSON.stringify({ summary: false, kind: true, size: false, source: false, handling: false, items: false }),
        JSON.stringify(shown.folds));
  check("the marker is drawn by us, > shut and v open, not the browser's triangle",
        shown.marks.filter((m) => m === '">"').length === 5 && shown.marks.filter((m) => m === '"v"').length === 1,
        JSON.stringify(shown.marks));
  check("a folded section is always an empty one: nothing with content is hidden",
        await evaluate(`[...document.querySelectorAll("dialog.editor details.fold:not([open])")].every((f) => {
          const body = f.querySelector(".fold-body");
          const written = body.querySelector("textarea, input:not([type=radio])");
          const chosen = body.querySelector("input[type=radio]:checked");
          const listed = body.querySelector("ul.items li");
          const marked = body.querySelector(".chip.on");
          return !chosen && !listed && !marked && (!written || written.value === "");
        })`));

  // It saves itself, like the record page does.
  await unfold("summary");
  check("opening a fold reveals its field and flips the marker",
        (await editor()).folds.summary && (await evaluate(`getComputedStyle(document.querySelector('dialog.editor [data-fold="summary"] > summary'), "::before").content`)) === '"v"');
  await click("#child-what");
  await type("spare forks");
  await waitFor(`${q("dialog.editor .autosave-state")}.textContent === "Saved"`, "the summary to save");
  shown = await editor();
  check("typing in it saves after the pause, with Undo naming the child's field",
        shown.undo === "Undo summary" && shown.mark === "same", JSON.stringify(shown));
  check("the server has it", (await api(`/boxes/${bare}`)).content_summary === "spare forks");
  check("and the container's row behind already says so, before anything closes",
        (await rowSays(bare)) === "spare forks", await rowSays(bare));

  // Two records in play: the modal's undo stack is the child's, the page's is
  // the container's, and neither reaches into the other.
  await evaluate(`document.querySelector('dialog.editor [data-fold="kind"] input[value="tub"]').labels[0].click()`);
  await waitFor(`document.querySelector('dialog.editor [name="kind"]:checked')?.value === "tub"`, "the kind to change");
  await sleep(700);
  check("a picker in the modal saves too, and the sections are rebuilt for the new kind",
        (await api(`/boxes/${bare}`)).kind === "tub" && (await editor()).undo === "Undo kind", JSON.stringify(await editor()));
  check("the page behind kept its own status line and its own undo, untouched",
        (await evaluate(`${q("#summary-form .undo")}.hidden`)) === true
          && (await evaluate(`${q("#summary-form .autosave-state")}.textContent`)) === "",
        await evaluate(`${q("#summary-form .autosave-state")}.textContent`));
  await click("dialog.editor .undo");
  await waitFor(`${q("dialog.editor .autosave-state")}.textContent === "Undone"`, "the undo in the modal");
  check("Undo in the modal takes back the child's change and nothing else",
        (await api(`/boxes/${bare}`)).kind === "bag" && (await api(`/boxes/${crate}`)).kind === "crate");

  // A change from elsewhere, taken in place.
  await evaluate(`document.querySelector("dialog.editor").__mark = "same"`);
  const shotBody = new FormData();
  shotBody.append("file", new Blob([readFileSync(photo)], { type: "image/png" }), "p.png");
  await fetch(`${base}/api/boxes/${bare}/photos`, { method: "POST", body: shotBody });
  await waitFor(`${q("dialog.editor #child-items")} && ${q("dialog.editor #child-items")}.children.length > 0`,
                "the model's items to reach the open modal", 250);
  shown = await editor();
  check("a change from elsewhere fills the modal in place, without rebuilding it under the hands",
        shown.mark === "same" && shown.open, JSON.stringify(shown));
  check("and what arrived is not left folded away", shown.folds.items === true, JSON.stringify(shown.folds));

  // Closing commits what is still waiting.
  await unfold("source");
  await click("#child-source-location");
  await type("shelf 3");
  await press("Escape", "Escape", 27);
  await waitFor(`!document.querySelector("dialog.editor")`, "Escape to close it");
  check("Escape closes it", (await evaluate("location.hash")) === onContainer);
  check("and what was still in the pause was committed on the way out",
        (await api(`/boxes/${bare}`)).source_location === "shelf 3", (await api(`/boxes/${bare}`)).source_location);

  // A record with something in it opens with those sections open.
  await openEditorOn(full);
  shown = await editor();
  check("a record with content opens showing it: only what is empty is folded",
        JSON.stringify(shown.folds) === JSON.stringify({ summary: true, kind: true, size: true, source: false, handling: true, items: true }),
        JSON.stringify(shown.folds));
  check("the handling it has is on show", await evaluate(`Boolean(${q("dialog.editor .chip.on")})`));

  // A scan while it is open goes nowhere, as for any modal.
  const hashNow = await evaluate("location.hash");
  await evaluate(`document.activeElement?.blur()`);
  await type(crate, 5);
  await press("Enter", "Enter", 13, "\r");
  await sleep(400);
  check("a scan while the editor is open goes nowhere", (await evaluate("location.hash")) === hashNow, await evaluate("location.hash"));

  // Adding an item from the modal, and the backdrop closing it.
  await unfold("items");
  await click("#child-add");
  await type("sugar tongs");
  await click("#child-add-go");
  await waitFor(`${q("dialog.editor #child-items")}.children.length === 2`, "the item to be added");
  check("an item can be added from the modal",
        (await api(`/boxes/${full}/items`)).some((i) => i.name === "sugar tongs"));
  await evaluate(`document.querySelector("dialog.editor").dispatchEvent(new MouseEvent("click", { bubbles: true }))`);
  await waitFor(`!document.querySelector("dialog.editor")`, "the backdrop to close it");
  check("a tap outside closes it", true);

  // Close, the third way out.
  await openEditorOn(full);
  await click("#child-close");
  await waitFor(`!document.querySelector("dialog.editor")`, "Close to close it");
  check("the Close button closes it, and leaves you on the container",
        (await evaluate("location.hash")) === onContainer, await evaluate("location.hash"));

  // The whole page is still the whole truth, and still one tap away.
  await openEditorOn(full);
  await click("#child-full");
  await waitFor(`location.hash === ${JSON.stringify(`#/b/${full}`)} && !document.querySelector("dialog.editor")`,
                "the link to the whole page");
  check("the link inside opens the record's own page, and the modal gets out of the way",
        (await evaluate(`Boolean(${q("#summary-form")}) && ${q("h1.code")}.textContent === ${JSON.stringify(full)}`)));

  check("nothing threw in the page", thrown.length === 0, thrown.join(" | "));
} catch (error) { check(`harness: ${error.message}`, false); }

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close(); chrome.kill(); await sleep(300);
try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* the OS will */ }
process.exit(failures ? 1 : 0);
