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
//          takes a change from elsewhere in place, shows the record's photos
//          as thumbs and opens the full viewer over itself on a tap -- one
//          Escape closing only the viewer, leaving the modal and its
//          half-typed field alone. And the live viewfinder in
//          the add dialog: it comes up by itself, the shutter keeps a frame
//          that really uploads, every track is stopped when the dialog closes,
//          and a refused camera leaves an honest line and a working file
//          picker.
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
  // A synthetic webcam, so the viewfinder in the add dialog can be looked at
  // and its shutter pressed. Permission is granted and denied through CDP
  // below rather than by a flag, so both paths can be checked in one run.
  ["--headless=new", "--disable-gpu", "--use-fake-device-for-media-stream",
   `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "about:blank"],
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
          return d.querySelector('.seg[data-name="kind"]') && d.querySelector("#adder-cam")
            && d.querySelector('#adder-shot[type=file]') && d.querySelector('.seg[data-name="source_room_id"]')
            && !d.querySelector("textarea") && !d.querySelector('.seg[data-name="destination_room_id"]')
            && !d.querySelector('.seg[data-name="size"]'); })()`));
  check("the picker no longer forces the camera app: the live one is the camera now",
        !(await evaluate(`document.querySelector("#adder-shot").hasAttribute("capture")`)));
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
  check("it says what the record is and where it is going",
        shown.where.startsWith("A bag inside") && shown.where.includes("going where"), shown.where);
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

  // --- photo thumbs in the modal, and the viewer over it ---
  //
  // "for the popup contents view, show image thumbs and show the full view on
  // demand." The strip is the record page's own (`photosPart`), so a tap
  // already opens `viewPhoto` -- over the modal, which is the interesting part.
  const otherShot = join(profile, "another.png");
  writeFileSync(otherShot, png(80, [70, 120, 190]));
  const pictured = (await api("/boxes", "POST", {
    kind: "box", content_summary: "crockery", parent_code: crate })).code;
  for (const file of [photo, otherShot]) {
    const body = new FormData();
    body.append("file", new Blob([readFileSync(file)], { type: "image/png" }), file);
    await fetch(`${base}/api/boxes/${pictured}/photos`, { method: "POST", body });
  }
  // Let the stub read them, so the viewer has something to show.
  for (let i = 0; i < 100; i++) {
    const done = (await api(`/boxes/${pictured}/photos`)).every((p) => p.analysis?.status === "done");
    if (done) break;
    await sleep(200);
  }
  const shelf = () => evaluate(`(() => { const m = document.querySelector("dialog.editor"); if (!m) return null;
    const figures = [...m.querySelectorAll(".shots figure")];
    const viewer = document.querySelector("dialog.viewer");
    return { thumbs: figures.length, keys: figures.map((f) => f.dataset.key).join(","),
             images: figures.filter((f) => f.querySelector(".pic img[src]")).length,
             fold: m.querySelector('[data-fold="photos"]')?.open ?? null,
             modalOpen: m.open, typed: m.querySelector("#child-what")?.value,
             undo: m.querySelector(".undo").hidden ? null : m.querySelector(".undo").textContent,
             line: m.querySelector(".autosave-state").textContent,
             items: [...m.querySelectorAll("#child-items li .name")].map((n) => n.textContent).join(","),
             viewerOpen: Boolean(viewer?.open),
             viewerHeading: viewer?.querySelector("h2")?.textContent,
             viewerItems: viewer ? viewer.querySelectorAll("ul.items li").length : 0,
             above: viewer ? (() => { const b = viewer.getBoundingClientRect();
               return document.elementFromPoint(b.left + b.width / 2, b.top + 20)?.closest("dialog")?.className; })() : null,
             focused: document.activeElement?.tagName + "/" + (document.activeElement?.closest("dialog")?.className || "page"),
           }; })()`);

  await goto(`#/b/${crate}`, `Boolean(${q(`#inside li[data-key="${pictured}"]`)})`);
  await openEditorOn(pictured);
  let pics = await shelf();
  const stored = await api(`/boxes/${pictured}/photos`);
  check("a record with photos shows them in the modal, one thumb each, open",
        pics.thumbs === stored.length && pics.images === stored.length && pics.fold === true,
        JSON.stringify({ ...pics, stored: stored.length }));
  check("each figure is keyed, so an update patches the strip instead of drawing a second one",
        pics.keys === stored.map((p) => p.id).join(",") && new Set(pics.keys.split(",")).size === stored.length,
        pics.keys);

  // Something half-typed, to watch it survive the viewer.
  await unfold("summary");
  await click("#child-what");
  await evaluate(`${q("#child-what")}.setSelectionRange(999, 999)`);
  await type(", and a jug");
  const halfTyped = await evaluate(`${q("#child-what")}.value`);

  await click("dialog.editor .shots figure .pic a");
  await waitFor(`Boolean(document.querySelector("dialog.viewer[open]"))`, "the viewer over the modal");
  await sleep(300);
  pics = await shelf();
  check("tapping a thumb opens the full viewer, above the modal rather than behind it",
        pics.viewerOpen && pics.modalOpen && pics.above === "viewer", JSON.stringify(pics));
  check("and it is the real viewer: what the model saw, and the closer look on offer",
        pics.viewerHeading === "Seen in this photo" && pics.viewerItems > 0
          && (await evaluate(`!document.querySelector("dialog.viewer [data-closer]").hidden`)), JSON.stringify(pics));

  // The viewer's own write buttons still work from in here.
  const beforeCloser = (await api(`/boxes/${pictured}/items`)).length;
  await click("dialog.viewer [data-closer]");
  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "done"
                 && document.querySelector("dialog.viewer h2").textContent === "Seen on a closer look"`,
                "the closer look to land in the viewer", 250);
  pics = await shelf();
  check("a closer look asked for from inside the modal runs and shows in the viewer",
        pics.viewerHeading === "Seen on a closer look" && pics.viewerOpen && pics.modalOpen, JSON.stringify(pics));
  check("and what it found reached the record under it: the modal's items grew",
        (await api(`/boxes/${pictured}/items`)).length > beforeCloser,
        `${beforeCloser} -> ${(await api(`/boxes/${pictured}/items`)).length}`);
  check("the strip underneath is still one figure per photo",
        (await shelf()).thumbs === stored.length, (await shelf()).keys);

  // A scan with two modals stacked still goes nowhere.
  const parked = await evaluate("location.hash");
  await evaluate(`document.activeElement?.blur()`);
  await type(crate, 5);
  await press("Enter", "Enter", 13, "\r");
  await sleep(400);
  check("a scan with the viewer over the modal goes nowhere", (await evaluate("location.hash")) === parked);

  // The heart of it: one Escape closes the top one only.
  await press("Escape", "Escape", 27);
  await sleep(400);
  pics = await shelf();
  check("one Escape closes the viewer and leaves the modal standing",
        !pics.viewerOpen && pics.modalOpen, JSON.stringify(pics));
  check("the half-typed summary is exactly as it was left",
        pics.typed === halfTyped, `${pics.typed} vs ${halfTyped}`);
  check("and the focus is back on the thumbnail that opened it",
        pics.focused === "A/editor", pics.focused);
  check("the modal's own session is untouched: nothing retired, nothing said to be saved by a viewer",
        pics.line !== "Saved" || pics.undo !== null, JSON.stringify({ line: pics.line, undo: pics.undo }));

  // The edit that was waiting still saves, and is still the child's to undo.
  await waitFor(`${q("dialog.editor .autosave-state")}.textContent === "Saved"`, "the waiting edit to save", 200);
  check("the edit made before the viewer opened saves afterwards, and Undo still names it",
        (await api(`/boxes/${pictured}`)).content_summary === halfTyped
          && (await shelf()).undo === "Undo summary", JSON.stringify(await shelf()));

  // A second Escape closes the modal, as ever.
  await press("Escape", "Escape", 27);
  await waitFor(`!document.querySelector("dialog.editor")`, "the second Escape to close the modal");
  check("a second Escape closes the modal itself", (await evaluate("location.hash")) === parked);
  check("and the container's row has the edit", (await evaluate(`${q(`#inside li[data-key="${pictured}"] .s`)}.textContent`)) === halfTyped,
        await evaluate(`${q(`#inside li[data-key="${pictured}"] .s`)}.textContent`));

  // A record with no photos has no photo section at all. (A fresh one: `bare`
  // was given a photo earlier, to watch a change from elsewhere arrive.)
  const unphotographed = (await api("/boxes", "POST", { kind: "bag", parent_code: crate })).code;
  await goto(`#/b/${crate}`, `Boolean(${q(`#inside li[data-key="${unphotographed}"]`)})`);
  await openEditorOn(unphotographed);
  check("a record with no photos has no photo section in the modal",
        (await evaluate(`Boolean(document.querySelector('dialog.editor [data-fold="photos"]'))`)) === false);
  await click("#child-close");
  await waitFor(`!document.querySelector("dialog.editor")`, "that modal to close");

  // --- the live viewfinder in the add dialog ---
  // Granting takes CDP's own permission enum; Browser.setPermission wants the
  // web-standard descriptor names and refuses "videoCapture" outright. With
  // nothing granted, headless Chrome refuses the camera -- which is the
  // refusal path, so resetting is how it is asked for.
  const camera = (allowed) => (allowed
    ? send("Browser.grantPermissions", { origin: base, permissions: ["videoCapture"] })
    : send("Browser.resetPermissions"));
  const viewfinder = () => evaluate(`(() => { const d = document.querySelector("dialog.adder"); if (!d) return null;
    const video = d.querySelector("#adder-cam");
    const track = video.srcObject?.getVideoTracks?.()[0];
    return { box: !d.querySelector("#adder-box").hidden, live: !video.hidden,
             frames: video.videoWidth, track: track ? track.readyState : null,
             shutter: !d.querySelector("#adder-shutter").hidden,
             retake: !d.querySelector("#adder-retake").hidden,
             still: !d.querySelector("#adder-still").hidden,
             stillWidth: d.querySelector("#adder-still").naturalWidth,
             picker: Boolean(d.querySelector("#adder-shot")),
             said: d.querySelector("#adder-photo").textContent }; })()`);

  await camera(true);
  await goto(`#/b/${crate}`, `Boolean(${q("#add-inside")})`);
  const insideNow = (await api(`/boxes/${crate}`)).children.length;
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the add dialog");
  await waitFor(`${q("#adder-cam")}.videoWidth > 0`, "the viewfinder to come up by itself", 200);
  let seen = await viewfinder();
  check("the camera comes up live in the dialog the moment it opens, with a shutter",
        seen.live && seen.box && seen.track === "live" && seen.frames > 0 && seen.shutter && !seen.still,
        JSON.stringify(seen));
  check("and the file picker is still there, as the other way to do it", seen.picker);

  await click("#adder-shutter");
  await waitFor(`!${q("#adder-still")}.hidden`, "the still it took");
  seen = await viewfinder();
  check("the shutter keeps a still and offers another go",
        seen.still && !seen.live && seen.retake && !seen.shutter, JSON.stringify(seen));
  check("the frame kept is no bigger than the server would keep anyway",
        seen.stillWidth > 0 && seen.stillWidth <= 2048, String(seen.stillWidth));
  check("and the line says what it is for", /read in the background/.test(seen.said), seen.said);

  await click("#adder-retake");
  seen = await viewfinder();
  check("another go puts the viewfinder back", seen.live && !seen.still && seen.shutter, JSON.stringify(seen));
  await click("#adder-shutter");
  await waitFor(`!${q("#adder-still")}.hidden`, "the second still");

  // The frame is what gets uploaded, and the camera is let go on the way out.
  await evaluate(`window.__track = document.querySelector("#adder-cam").srcObject.getVideoTracks()[0]`);
  await click("#adder-add");
  await waitFor(`!document.querySelector("dialog.adder")`, "Add to close the dialog", 200);
  check("the camera is released when the dialog closes: every track stopped",
        (await evaluate(`window.__track.readyState`)) === "ended", await evaluate(`window.__track.readyState`));
  const withPhoto = (await api(`/boxes/${crate}`)).children.at(-1);
  check("it made the record", (await api(`/boxes/${crate}`)).children.length === insideNow + 1);
  const taken = await api(`/boxes/${withPhoto.code}/photos`);
  check("and the photo taken in the dialog is on it, being read",
        taken.length === 1 && taken[0].bytes > 0 && Boolean(taken[0].analysis),
        JSON.stringify(taken.map((t) => [t.bytes, t.analysis?.status])));

  // Refused: an ordinary outcome, not an error state.
  await camera(false);
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the dialog again");
  await waitFor(`/declined/.test(${q("#adder-photo")}.textContent)`, "the refusal to be said", 200);
  seen = await viewfinder();
  check("a refused camera says so in a line, with no dead grey box",
        !seen.box && !seen.live && seen.track === null, JSON.stringify(seen));
  check("and points at the way that still works, which is still there",
        /[Cc]hoose a photo/.test(seen.said) && seen.picker, seen.said);
  check("nothing about it reads as an error", !/error|failed/i.test(seen.said), seen.said);
  await click("dialog.adder [value=no]");
  await waitFor(`!document.querySelector("dialog.adder")`, "the refusal dialog to close");
  await camera(true);

  // --- search results, grouped and indented, on a 320px screen ------------
  //
  // "in search results, put the parent box first. indent subitems. then we
  // don't need in B-xxxx." A fresh chain, five deep, so the cap on the indent
  // is pressed rather than reasoned about -- and at the narrowest screen this
  // app is used on, because that is where an indent goes wrong.
  let deepest = (await api("/boxes", "POST", { kind: "crate", content_summary: "outermost" })).code;
  const chain = [deepest];
  for (const level of ["second", "third", "fourth"]) {
    deepest = (await api("/boxes", "POST", { kind: "box", content_summary: level, parent_code: deepest })).code;
    chain.push(deepest);
  }
  const found = (await api("/boxes", "POST", { kind: "bag", content_summary: "a vermillion zither", parent_code: deepest })).code;
  chain.push(found);

  await send("Emulation.setDeviceMetricsOverride", { width: 320, height: 640, deviceScaleFactor: 0, mobile: true });
  await goto("#/search/zither", `${q("#boxlist")} && ${q("#boxlist")}.children.length === 5`);
  await sleep(200);
  const drawn = await evaluate(`(() => {
    const rows = Array.from(document.querySelectorAll("#boxlist li"));
    const doc = document.documentElement;
    return {
      keys: rows.map((li) => li.dataset.key),
      inset: rows.map((li) => Math.round(parseFloat(getComputedStyle(li.querySelector("a")).paddingLeft))),
      context: rows.map((li) => li.hasAttribute("data-context")),
      status: rows.map((li) => li.querySelector(".st").textContent),
      at: document.querySelectorAll("#boxlist .at").length,
      onList: getComputedStyle(rows[0]).borderBottomWidth,
      onLink: getComputedStyle(rows[0].querySelector("a")).borderBottomWidth,
      overflow: doc.scrollWidth - doc.clientWidth,
      right: Math.max(...rows.map((li) => li.querySelector(".w").getBoundingClientRect().right)),
    };
  })()`);

  check("search: the containers come first, outermost first, the match last",
        JSON.stringify(drawn.keys) === JSON.stringify(chain), drawn.keys.join(" "));
  check("search: each step is indented further than the one it is inside",
        drawn.inset[0] === 0 && drawn.inset[1] > 0 && drawn.inset[2] > drawn.inset[1] && drawn.inset[3] > drawn.inset[2],
        drawn.inset.join(" "));
  check("search: the indent is capped, so a deeper chain stops walking right",
        drawn.inset[4] === drawn.inset[3], drawn.inset.join(" "));
  check("search: the containers are marked as context, the match is not",
        JSON.stringify(drawn.context) === JSON.stringify([true, true, true, true, false]), JSON.stringify(drawn.context));
  check("search: nothing says 'in B-xxxx' any more", drawn.at === 0, String(drawn.at));
  check("search: a row inside something shows no packing status, a top-level one does",
        drawn.status[0] !== "" && drawn.status.slice(1).every((s) => s === ""), JSON.stringify(drawn.status));
  check("search: the rule under a row is the link's, so it steps in with the indent",
        drawn.onList === "0px" && drawn.onLink !== "0px", `${drawn.onList} / ${drawn.onLink}`);
  check("search: at 320px nothing is pushed off the right-hand edge",
        drawn.overflow <= 0 && drawn.right <= 320, `overflow ${drawn.overflow}, right edge ${drawn.right}`);
  await send("Emulation.clearDeviceMetricsOverride");

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
