#!/usr/bin/env node
// Purpose: two things about a record inside a container, pressed for real.
//          It goes where the container goes: no destination row of its own,
//          the band and a line saying whose room it is, the row back in place
//          when it is taken out. And fragile climbs: marking it fragile offers
//          to mark the containers too (accepted here, and seen on the
//          container), clearing it touches nothing, putting a fragile thing
//          inside something offers again, and so does creating one inside with
//          Fragile ticked. Uses the page's own dialog, clicked, not stubbed.
// Date:    2026-09-20
// Usage:   node scripts/claude/nesting_check.mjs <base-url>
//          WRITES: creates records and marks them. Refuses the live service.
//          scripts/claude/browser_checks.sh runs it on a throwaway.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

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
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails.exception?.description || "?");
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
    const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
  if (!spot) throw new Error(`nothing to click: ${selector}`);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send("Input.dispatchMouseEvent", { type, x: spot.x, y: spot.y, button: "left", clickCount: 1 });
  }
  await sleep(80);
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

  // Take it out: its own row comes back, in place, and the band empties.
  await evaluate(`${q("#summary-form [name=content_summary]")}.__mark = "kept"`);
  await click("#container-take");
  await waitFor(`${q("#container .autosave-state")}.textContent === "Saved" && !${q('.seg[data-name="destination_room_id"]')}.hidden`, "taking it out");
  check("taken out: the destination row is back, enabled, without a redraw",
        JSON.stringify(await roomRow()) === JSON.stringify({ hidden: false, disabled: false })
          && (await evaluate(`${q("#summary-form [name=content_summary]")}.__mark`)) === "kept", JSON.stringify(await roomRow()));
  check("taken out: no inherited room, no band, no line", (await band()) === null && (await goesWith()) === null);
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
