#!/usr/bin/env node
// Purpose: label copies and the thin-label question, pressed for real. Settings
//          stores a number for each kind of thing (a box 2, a chair 1); the
//          record page shows its own kind's and can override it for one print;
//          a complete label prints without asking; one with no
//          contents or no destination room asks first (Cancel focused, "no"
//          prints nothing, "yes" sends allow_empty); the new-record form asks
//          *before* creating anything; the stub never asks and prints one.
// Date:    2026-09-18
// Usage:   node scripts/claude/copies_check.mjs <base-url>
//          WRITES: creates records and prints to the fake printer. Refuses the
//          live service. scripts/claude/browser_checks.sh runs it on a throwaway.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const NAME = "copies_check";
// These checks create records, print (to the fake printer) and upload photos.
// Never against the live service: the real database, and real tape.
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
const PORT = Number(process.env.CDP_PORT) || 9337;
const profile = mkdtempSync(join(tmpdir(), "copies-check-"));
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
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const msg = JSON.parse(m.data); pending.get(msg.id)?.(msg); pending.delete(msg.id); };
const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description);
  return r.result.result.value;
};
const waitFor = async (selector, gone = false) => {
  for (let i = 0; i < 100; i++) {
    if ((await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`)) !== gone) return;
    await sleep(100);
  }
  throw new Error(`${gone ? "never went" : "never appeared"}: ${selector}`);
};
const goto = async (hash, selector) => { await evaluate(`location.hash = ${JSON.stringify(hash)}`); await sleep(300); await waitFor(selector); };
const api = async (path, method = "GET", body) =>
  (await fetch(`${base}/api${path}`, { method, headers: { "content-type": "application/json" }, body: body && JSON.stringify(body) })).json();

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);
const prints = () => evaluate(`JSON.stringify(window.__prints)`).then(JSON.parse);
const dialogText = () => evaluate(`document.querySelector("dialog.ask")?.open ? document.querySelector("dialog.ask").innerText : null`);

try {
  const rooms = await api("/rooms");
  const room = rooms.find((r) => r.kind !== "source").id;
  const full = (await api("/boxes", "POST", { content_summary: "pots and pans", destination_room_id: room })).code;
  const noRoom = (await api("/boxes", "POST", { content_summary: "pots and pans" })).code;
  const bare = (await api("/boxes", "POST", {})).code;
  const chair = (await api("/boxes", "POST", { kind: "furniture", content_summary: "armchair", destination_room_id: room })).code;
  const copiesOf = async (kind) => (await api("/settings/kinds")).find((k) => k.kind === kind).copies;
  // Start from the numbers as shipped, so this can be run twice against one
  // throwaway: it changes a box's number below, and would find it changed.
  const SHIPPED = { box: 2, tub: 2, crate: 2, bag: 1, item: 1, furniture: 1 };
  for (const [kind, copies] of Object.entries(SHIPPED)) await api("/settings/kind-copies", "PUT", { kind, copies });

  await send("Page.enable");
  await send("Page.navigate", { url: `${base}/#/settings` });
  await waitFor("#kind-copies");
  await evaluate(`(() => { window.__prints = []; const f = window.fetch;
    window.fetch = (u, o = {}) => { if (String(u).includes("/labels/print")) window.__prints.push(JSON.parse(o.body)); return f(u, o); }; })()`);

  // --- settings: a number for each kind of thing ---
  const shown = JSON.parse(await evaluate(`JSON.stringify(Object.fromEntries(
    [...document.querySelectorAll("#kind-copies input[type=number]")].map((f) => [f.name, f.value])))`));
  check("settings: each kind shows the number asked for",
        JSON.stringify(shown) === JSON.stringify({ box: "2", tub: "2", crate: "2", bag: "1", item: "1", furniture: "1" }),
        JSON.stringify(shown));
  check("settings: every number has a label naming its kind",
        await evaluate(`[...document.querySelectorAll("#kind-copies input[type=number]")].every((f) => f.labels?.length === 1 && f.labels[0].textContent.trim() !== "")`));
  check("settings: the single number for everything is gone", !(await evaluate(`Boolean(document.getElementById("printing") || document.getElementById("label-copies"))`)));
  await evaluate(`(() => { window.__puts = []; const f = window.fetch;
    window.fetch = (u, o = {}) => { if (String(u).includes("/settings/kind-copies")) window.__puts.push(JSON.parse(o.body)); return f(u, o); }; })()`);
  await evaluate(`(() => { const f = document.getElementById("copies-box"); f.value = "3";
    f.dispatchEvent(new Event("input", { bubbles: true })); document.querySelector("#kind-copies button[type=submit]").click(); })()`);
  await sleep(600);
  check("settings: saving three for a box is stored", (await copiesOf("box")) === 3);
  check("settings: only the kind that changed was sent",
        (await evaluate(`JSON.stringify(window.__puts)`)) === JSON.stringify([{ kind: "box", copies: 3 }]),
        await evaluate(`JSON.stringify(window.__puts)`));
  check("settings: the others are untouched", (await copiesOf("furniture")) === 1 && (await copiesOf("tub")) === 2);
  check("settings: and says so", /box 3/.test(await evaluate(`document.getElementById("say").textContent`)),
        await evaluate(`document.getElementById("say").textContent`));
  check("settings: the saved number is the form's new baseline",
        await evaluate(`(() => { const f = document.getElementById("copies-box"); return f.dataset.initial === "3" && f.value === "3"; })()`));
  // Nonsense never leaves the page: the field's own min and max stop the form
  // being submitted at all (the server would refuse it too; its tests say so).
  await evaluate(`(() => { const f = document.getElementById("copies-bag"); f.value = "99";
    f.dispatchEvent(new Event("input", { bubbles: true })); document.getElementById("kind-copies").requestSubmit(); })()`);
  await sleep(500);
  check("settings: a number out of range is stopped by the form, and nothing is sent or stored",
        (await copiesOf("bag")) === 1 && (await evaluate(`window.__puts.length`)) === 1
          && (await evaluate(`document.getElementById("copies-bag").validity.rangeOverflow`)),
        await evaluate(`JSON.stringify(window.__puts)`));

  // --- a complete label: no question asked ---
  await goto(`#/b/${full}`, "#print");
  check("record page: a box's copies starts at the number stored for boxes", (await evaluate(`document.getElementById("copies").value`)) === "3");
  await evaluate(`document.getElementById("print").click()`);
  await sleep(800);
  check("complete label: prints without asking", (await dialogText()) === null && (await prints()).length === 1);
  check("complete label: sends the copies shown, and no override",
        JSON.stringify((await prints())[0]) === JSON.stringify({ codes: [full], copies: 3, allow_empty: false }),
        JSON.stringify((await prints())[0]));
  check("complete label: the count rose by the copies", (await api(`/boxes/${full}`)).label_print_count === 3);

  // --- changing copies for one print, without dirtying the page ---
  await goto(`#/b/${full}`, "#print");
  await evaluate(`(() => { const f = document.getElementById("copies"); f.value = "1"; f.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  check("copies: changing it does not leave the page looking half-edited",
        (await evaluate(`(() => { const f = document.getElementById("copies"); return f.dataset.initial === f.value; })()`)));
  await evaluate(`document.getElementById("print").click()`);
  await sleep(800);
  check("copies: one print can ask for one", (await prints()).at(-1).copies === 1);
  check("copies: the stored number is untouched", (await copiesOf("box")) === 3);

  // --- a single thing has its own number ---
  await goto(`#/b/${chair}`, "#print");
  check("record page: a chair's copies starts at the number stored for furniture",
        (await evaluate(`document.getElementById("copies").value`)) === "1");
  await evaluate(`document.getElementById("print").click()`);
  await sleep(800);
  check("a chair prints one, without being told", (await prints()).at(-1).copies === 1 && (await api(`/boxes/${chair}`)).label_print_count === 1,
        JSON.stringify((await prints()).at(-1)));

  // --- no room ---
  await goto(`#/b/${noRoom}`, "#print");
  let before = (await prints()).length;
  await evaluate(`document.getElementById("print").click()`);
  await waitFor("dialog.ask");
  let text = await dialogText();
  check("no room: it asks, and names the room", /no destination room/i.test(text) && !/nothing is written/i.test(text), text);
  check("no room: Cancel has the focus", (await evaluate(`document.activeElement?.value`)) === "no");
  await evaluate(`document.querySelector("dialog.ask [value=no]").click()`);
  await waitFor("dialog.ask", true);
  await sleep(300);
  check("no room: saying no prints nothing", (await prints()).length === before);

  // --- nothing at all ---
  await goto(`#/b/${bare}`, "#print");
  before = (await prints()).length;
  await evaluate(`document.getElementById("print").click()`);
  await waitFor("dialog.ask");
  text = await dialogText();
  check("bare: it names both problems", /nothing is written/i.test(text) && /no destination room/i.test(text), text);
  check("bare: the old tick box is gone", !(await evaluate(`Boolean(document.getElementById("print-anyway"))`)));
  await evaluate(`document.querySelector("dialog.ask [value=yes]").click()`);
  await waitFor("dialog.ask", true);
  await sleep(900);
  const sent = (await prints()).at(-1);
  check("bare: saying yes prints, with the override", (await prints()).length === before + 1 && sent.allow_empty === true, JSON.stringify(sent));
  check("bare: and the server accepted it", (await api(`/boxes/${bare}`)).label_print_count === 3 && !(await evaluate(`document.getElementById("oops")?.open`)));

  // --- the new-record form ---
  await goto("#/new", "#create-print");
  const countBefore = (await api("/boxes?limit=100")).length;
  await evaluate(`document.getElementById("create-print").click()`);
  await waitFor("dialog.ask");
  check("new form: asks before creating anything", /nothing is written/i.test(await dialogText()));
  await evaluate(`document.querySelector("dialog.ask [value=no]").click()`);
  await waitFor("dialog.ask", true);
  await sleep(400);
  check("new form: saying no creates nothing and leaves you on the form",
        (await api("/boxes?limit=100")).length === countBefore && (await evaluate(`location.hash`)) === "#/new");

  await evaluate(`document.getElementById("create-stub").click()`);
  await sleep(1200);
  check("new form: the stub never asks", (await dialogText()) === null && (await evaluate(`location.hash`)).startsWith("#/b/"));
  check("new form: and prints one", (await prints()).at(-1).stub === true && (await api(`/boxes/${(await evaluate(`location.hash`)).slice(4)}`)).label_print_count === 1);
} catch (error) { check(`harness: ${error.message}`, false); }

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close(); chrome.kill(); await sleep(200); rmSync(profile, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
