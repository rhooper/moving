#!/usr/bin/env node
// Purpose: a keyboard-wedge barcode reader against the real page. It "types"
//          a box number (the label's Code 128) or a box URL (the QR) and
//          presses Return -- into the search box, at the page with nothing
//          focused, and with the Print button focused, which is the dangerous
//          one: the reader's Return must not press it.
//          Uses REAL key events (CDP Input.dispatchKeyEvent), so default
//          actions actually happen if the page fails to stop them, and counts
//          every non-GET request to prove nothing was written or printed.
// Date:    2026-09-18
// Usage:   node scripts/claude/wedge_check.mjs [base-url] [CODE-A CODE-B]
//          defaults: http://127.0.0.1:8788 and the two newest live records
//          (`make run` then `make ui-check` runs this after ui_check.mjs)
//
// Read-only. Needs Node 22+ (global WebSocket) and Google Chrome. No npm.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2] || "http://127.0.0.1:8788";
// Two records that are not in the bin: a binned one has Restore where this
// expects Delete. The list endpoint only returns live ones.
let [A, B] = [process.argv[3], process.argv[4]];
if (!A || !B) {
  const live = await (await fetch(`${base}/api/boxes?limit=2`)).json();
  if (live.length < 2) {
    console.error("wedge_check: needs two live records to scan between");
    process.exit(1);
  }
  [A, B] = [live[0].code, live[1].code];
}
const profile = mkdtempSync(join(tmpdir(), "wedge-check-"));
// Overridable: this is not the only headless Chrome on the machine.
const PORT = Number(process.env.CDP_PORT) || 9336;
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
const goto = async (hash) => { await evaluate(`location.hash = ${JSON.stringify(hash)}`); await sleep(500); };
const waitFor = async (selector) => {
  for (let i = 0; i < 100; i++) {
    if (await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`)) return;
    await sleep(100);
  }
  throw new Error(`never appeared: ${selector}`);
};

// Type like a reader: fast, real key events, then Return.
async function scan(text) {
  for (const ch of text) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
  }
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await sleep(600);
}

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);
const hash = () => evaluate("location.hash");
const host = "https://moving.example.ts.net";

try {
await send("Page.enable");
await send("Page.navigate", { url: `${base}/#/` });
await sleep(1500);
// Count anything that would print or write.
await evaluate(`(() => { window.__writes = []; const f = window.fetch;
  window.fetch = (u, o = {}) => { if ((o.method || "GET") !== "GET") window.__writes.push((o.method) + " " + u); return f(u, o); }; })()`);

// --- in the search box ---
const inSearch = async (text) => {
  await goto("#/");
  await waitFor("#search [name=q]");
  await evaluate(`document.querySelector("#search [name=q]").focus()`);
  await scan(text);
  return hash();
};
check("search box: a box number + Return opens the box", (await inSearch(A)) === `#/b/${A}`, await hash());
check("search box: lower case still finds it", (await inSearch(A.toLowerCase())) === `#/b/${A}`, await hash());
check("search box: a box URL + Return opens the box", (await inSearch(`${host}/b/${B}`)) === `#/b/${B}`, await hash());
check("search box: a word is still a search", (await inSearch("cables")) === "#/search/cables", await hash());
check("search box: an unknown number is a search, not a dead page",
      (await inSearch("B-9999")) === "#/search/B-9999", await hash());

// --- nothing focused ---
const atPage = async (text) => {
  await goto("#/");
  await waitFor("#search [name=q]");
  await evaluate(`document.activeElement?.blur()`);
  await scan(text);
  return hash();
};
check("nothing focused: a scanned number opens the box", (await atPage(A)) === `#/b/${A}`, await hash());
check("nothing focused: a scanned QR (URL, slashes and all) opens the box",
      (await atPage(`${host}/b/${B}`)) === `#/b/${B}`, await hash());
check("nothing focused: a mis-scan shows as a search", (await atPage("B-9999")) === "#/search/B-9999", await hash());

// --- the dangerous one: a button has the focus ---
await goto(`#/b/${B}`);
await waitFor("#print");
await evaluate(`document.getElementById("print").focus()`);
check("(setup) the Print button really has the focus",
      (await evaluate(`document.activeElement?.id`)) === "print");
await scan(A);
check("Print focused: the scan opens the other box", (await hash()) === `#/b/${A}`, await hash());
check("Print focused: the reader's Return did NOT press Print",
      (await evaluate(`window.__writes.filter((w) => w.includes("/labels/print")).length`)) === 0,
      await evaluate(`JSON.stringify(window.__writes)`));

// --- a pushbutton has the focus: it is a button, though it is an <input> ---
// Whichever radio was tapped last keeps the focus, as Print does. Taken for
// typing, the scan would be dropped and its Return would submit the radio's
// form.
await goto(`#/b/${B}`);
await waitFor('.seg[data-name="kind"] input:checked');
await evaluate(`document.querySelector('.seg[data-name="kind"] input:checked').focus()`);
check("(setup) a pushbutton really has the focus",
      (await evaluate(`document.activeElement?.type`)) === "radio");
await scan(A);
check("pushbutton focused: the scan opens the other box", (await hash()) === `#/b/${A}`, await hash());
await goto("#/new");
await waitFor('#new .seg[data-name="kind"] input:checked');
await evaluate(`document.querySelector('#new .seg[data-name="kind"] input:checked').focus()`);
await scan(A);
check("pushbutton focused on the new-record form: the scan opens the box, and creates nothing",
      (await hash()) === `#/b/${A}`, await hash());

// --- a modal is a question being asked ---
await goto(`#/b/${B}`);
await waitFor("#delete");
await evaluate(`document.getElementById("delete").click()`);
await sleep(300);
await evaluate(`document.activeElement?.blur()`);
const before = await hash();
for (const ch of A) {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
}
await sleep(200);
check("a modal is open: the scan goes nowhere", (await hash()) === before, await hash());
await evaluate(`document.querySelector("dialog.ask")?.close("no")`);

check("nothing was written or printed at any point",
      (await evaluate(`window.__writes.length`)) === 0, await evaluate(`JSON.stringify(window.__writes)`));

} catch (error) { check(`harness: ${error.message}`, false); }

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close(); chrome.kill(); await sleep(200); rmSync(profile, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
