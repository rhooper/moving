#!/usr/bin/env node
// Purpose: Fragile turning itself on, pressed for real ("given it contains a
//          mug or figurine, auto-add fragile"). Typing a breakable item turns
//          the chip on where it is, says so, and does not redraw the page;
//          once a person clears it, the next breakable item leaves it off;
//          "From contents" on a record that already held a mug marks it; and
//          a change from another device lands on the chip in place.
// Date:    2026-10-04
// Usage:   node scripts/claude/fragile_check.mjs <base-url>
//          WRITES: creates records. Refuses the live service.
//          scripts/claude/browser_checks.sh runs it on a throwaway.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const NAME = "fragile_check";
// This writes, so never port 8787 -- the live service, with the real database
// -- nor any host that is not this machine.
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
const profile = mkdtempSync(join(tmpdir(), "fragile-check-"));
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
const until = async (expression, what, tries = 100) => {
  for (let i = 0; i < tries; i++) {
    if (await evaluate(`Boolean(${expression})`)) return true;
    await sleep(100);
  }
  throw new Error(`never happened: ${what}`);
};
// Another device: its own X-Client-Id, so this page does not take the event
// as its own echo.
const api = async (path, method = "GET", body) =>
  (await fetch(`${base}/api${path}`, {
    method,
    headers: { "content-type": "application/json", "x-client-id": "another-phone" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })).json();

const CHIP = `document.querySelector('[data-flag="fragile"]')`;
const chipOn = () => evaluate(`${CHIP}?.classList.contains("on") && ${CHIP}.getAttribute("aria-pressed") === "true"`);
const said = () => evaluate(`document.getElementById("say")?.hidden ? "" : document.getElementById("say")?.textContent`);
// Set on the form after each draw: a redraw replaces the element and loses it.
const markPage = () => evaluate(`document.getElementById("add-item").__drawn = "this one"`);
const sameDraw = () => evaluate(`document.getElementById("add-item")?.__drawn === "this one"`);
const open = async (code) => {
  await evaluate(`location.hash = ${JSON.stringify(`#/b/${code}/edit`)}`);
  await until(`document.querySelector("h1.code")?.textContent === ${JSON.stringify(code)}`, `${code}'s editor`);
  await until(`document.getElementById("add-item")`, "the add-items form");
  await sleep(400);   // the event socket connecting
  await markPage();
};
const add = async (names) => {
  await evaluate(`(() => { const f = document.getElementById("add-item");
    f.querySelector("[name=name]").value = ${JSON.stringify(names)}; f.requestSubmit(); })()`);
  await until(`document.getElementById("add-item").querySelector("[name=name]").value === ""`, `adding ${names}`);
  await sleep(300);
};

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);

try {
  await send("Page.navigate", { url: `${base}/` });
  await sleep(800);

  // --- typing a breakable thing -----------------------------------------
  const one = (await api("/boxes", "POST", {})).code;
  await open(one);
  check("a new record starts not fragile", !(await chipOn()));
  await add("weight plate");
  check("a weight plate does not mark it", !(await chipOn()) && !(await api(`/boxes/${one}`)).fragile);
  await add("black devs mug");
  await until(`${CHIP}?.classList.contains("on")`, "the chip to turn on");
  check("a mug turns the chip on", await chipOn());
  check("and says why", /Marked fragile: it holds black devs mug/.test(await said()), await said());
  check("without redrawing the page", await sameDraw());
  check("the server has it", (await api(`/boxes/${one}`)).fragile === 1);

  // --- a person's clearing wins -----------------------------------------
  await evaluate(`${CHIP}.click()`);
  await until(`!${CHIP}?.classList.contains("on")`, "the chip to turn off");
  check("pressing the chip clears it", !(await api(`/boxes/${one}`)).fragile);
  await markPage();
  await add("small vase");
  await sleep(500);
  check("after that, a vase leaves it off", !(await chipOn()) && !(await api(`/boxes/${one}`)).fragile);

  // --- From contents, on a record that already held a mug ----------------
  const older = (await api("/boxes", "POST", {})).code;
  for (const name of ["weight plate", "remote organizer", "diy vacuum", "leather hide"]) {
    await api(`/boxes/${older}/items`, "POST", { name });
  }
  // A rename is not an item landing: this is a record from before marking.
  const plate = (await api(`/boxes/${older}/items`)).find((item) => item.name === "weight plate");
  await api(`/items/${plate.id}`, "PATCH", { name: "axolotl figurine" });
  check("set up: a record with a figurine that is not fragile", !(await api(`/boxes/${older}`)).fragile);
  await open(older);
  await evaluate(`document.getElementById("suggest").click()`);
  await until(`${CHIP}?.classList.contains("on")`, "From contents to mark it");
  check("From contents marks it fragile", (await api(`/boxes/${older}`)).fragile === 1);
  check("and says why", /Marked fragile: it holds axolotl figurine/.test(await said()), await said());
  check("and still writes the summary", ((await api(`/boxes/${older}`)).content_summary || "") !== "");

  // --- another device -------------------------------------------------------
  const third = (await api("/boxes", "POST", { content_summary: "kettle" })).code;
  await open(third);
  await api(`/boxes/${third}`, "PATCH", { fragile: true });
  await until(`${CHIP}?.classList.contains("on")`, "another phone's change to arrive");
  check("fragile from another device lands on the chip", await chipOn());
  check("in place, not by redrawing the page", await sameDraw());
} catch (error) { check(`harness: ${error.message}`, false); }

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close(); chrome.kill(); await sleep(200); rmSync(profile, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
