#!/usr/bin/env node
// Purpose: an open page catching up with a deploy, pressed for real. Runs its
//          own throwaway server and restarts it under a new MOVING_REVISION --
//          which is what a deploy is, from a phone's side -- then watches what
//          the open page does:
//          - revision `unknown` (a dev server) never reloads, and never asks;
//          - an idle page reloads itself once, lands on the same record, and is
//            running the new code rather than a shell the service worker kept;
//          - half-typed text is not reloaded over: the banner says the app was
//            updated, a tap while the save still cannot land waits for it, and
//            the text is on the server -- read back from the API -- before the
//            page reloads;
//          - an open dialog with the camera live is not reloaded under;
//          - leaving a record is a moment to reload, landing where it was going;
//          - a /health that cannot be read changes nothing, and one that keeps
//            disagreeing costs one reload, not a loop;
//          - a hidden page does not ask, even when its socket reconnects, and
//            asks at once when it is seen again.
// Date:    2026-09-21
// Usage:   node scripts/claude/reload_check.mjs
//          WRITES, but only to its own server: it starts one on a free
//          loopback port with its own database, and stops exactly that process
//          group. CDP_PORT picks the Chrome debugging port.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, openSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

const NAME = "reload_check";
const ROOT = process.cwd();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PORT = await new Promise((resolve, reject) => {
  const probe = createServer();
  probe.on("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});
// It restarts the server it talks to. Never the live one.
if (PORT === 8787) { console.error(`${NAME}: refusing port 8787`); process.exit(2); }
const base = `http://127.0.0.1:${PORT}`;

const T = mkdtempSync(join(tmpdir(), "reload-check-"));
// Its own of everything: browser_checks.sh exports its own server's paths,
// and this one must not share that database.
const env = {
  ...process.env,
  MOVING_DB_PATH: join(T, "t.db"), MOVING_PHOTO_DIR: join(T, "photos"),
  MOVING_LABEL_PREVIEW_DIR: join(T, "prev"), MOVING_BACKUP_DIR: join(T, "bk"),
  MOVING_PRINTER_BACKEND: "fake", MOVING_VISION_PROVIDER: "stub", MOVING_VISION_STUB_SECONDS: "1",
};
const venv = join(ROOT, ".venv", "bin", "moving");
const moving = existsSync(venv) ? [venv] : ["uv", "run", "moving"];
const log = openSync(join(T, "server.log"), "a");

const run = (args, extra = {}) => new Promise((resolve) => {
  const child = spawn(moving[0], [...moving.slice(1), ...args], { cwd: ROOT, env: { ...env, ...extra }, stdio: "ignore" });
  child.on("exit", resolve);
});
await run(["seed-rooms"]);   // migrate before a browser races to

let server = null;
const health = async () => {
  try { return (await (await fetch(`${base}/health`)).json()).revision; } catch { return null; }
};
// `unknown` is named rather than left unset: unset reads var/deployed-revision,
// which in the main checkout is the live deploy's.
async function serve(revision) {
  // Its own process group, so stopping it stops the server and nothing else.
  server = spawn(moving[0], [...moving.slice(1), "serve", "--port", String(PORT)],
    { cwd: ROOT, env: { ...env, MOVING_REVISION: revision }, stdio: ["ignore", log, log], detached: true });
  for (let i = 0; i < 160; i++) { if ((await health()) === revision) return; await sleep(125); }
  throw new Error(`the server did not come up as ${revision}`);
}
async function stop() {
  if (!server) return;
  const gone = new Promise((r) => server.once("exit", r));
  try { process.kill(-server.pid, "SIGTERM"); } catch { /* already gone */ }
  const quick = await Promise.race([gone.then(() => true), sleep(8000).then(() => false)]);
  if (!quick) { try { process.kill(-server.pid, "SIGKILL"); } catch { /* gone */ } await gone; }
  server = null;
  for (let i = 0; i < 80 && (await health()) !== null; i++) await sleep(100);
}

const CDP = Number(process.env.CDP_PORT) || 9362;
const profile = mkdtempSync(join(tmpdir(), "reload-check-chrome-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", "--use-fake-device-for-media-stream",
   `--remote-debugging-port=${CDP}`, `--user-data-dir=${profile}`, "about:blank"],
  { stdio: "ignore" });

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);
let ws;
let id = 0;
const pending = new Map();
const thrown = [];
let loads = 0;                       // real page loads, not hash changes
let sockets = 0;                     // websocket handshakes the page completed
const healthAsked = [];              // /health requests the page made
let failPatches = false;             // saves refused at the network
let healthSays = null;               // "fail", or a revision to answer with
const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description);
  return r.result?.result?.value;
};
// Mid-reload there is no page to ask: that reads as "not yet".
const waitFor = async (expression, what, ms = 40000) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try { if (await evaluate(expression)) return; } catch { /* between documents */ }
    await sleep(100);
  }
  throw new Error(`timed out: ${what}`);
};
// And one for what only this side knows, such as how many loads it has seen.
const until = async (test, what, ms = 40000) => {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) { if (test()) return; await sleep(100); }
  throw new Error(`timed out: ${what}`);
};
const q = (selector) => `document.querySelector(${JSON.stringify(selector)})`;
const click = async (selector) => {
  const spot = await evaluate(`(() => { const el = ${q(selector)}; if (!el) return null; el.scrollIntoView({ block: "center" });
    const b = el.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, seen: b.width > 0 && b.height > 0 }; })()`);
  if (!spot) throw new Error(`nothing to click: ${selector}`);
  if (!spot.seen) throw new Error(`not visible to click: ${selector}`);
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send("Input.dispatchMouseEvent", { type, x: spot.x, y: spot.y, button: "left", clickCount: 1 });
  }
  await sleep(80);
};
const type = async (text) => {
  for (const ch of text) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
    await sleep(15);
  }
};
const api = async (path, method = "GET", body) =>
  (await fetch(`${base}/api${path}`, {
    method, headers: { "content-type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) })).json();
// What the page says about itself: the entry point's ?v= is the shell it was
// served, which is the thing a stale service worker would get wrong.
const shell = () => evaluate(`document.querySelector('script[type=module]')?.getAttribute("src")`);
const banner = () => evaluate(`(() => { const b = ${q("#live")}; return b && !b.hidden ? b.textContent : null; })()`);
const drawn = (code) => `${q("#summary-form")} && document.querySelector("h1.code")?.textContent.includes(${JSON.stringify(code)})`;
// The page's own path to a check, as a phone picked up again would take it.
const lookAgain = () => evaluate(`document.dispatchEvent(new Event("visibilitychange"))`);

try {
  let wsUrl;
  for (let i = 0; i < 150 && !wsUrl; i++) {
    try { wsUrl = (await (await fetch(`http://127.0.0.1:${CDP}/json`)).json()).find((p) => p.type === "page")?.webSocketDebuggerUrl; } catch { /* not up */ }
    await sleep(100);
  }
  ws = new WebSocket(wsUrl);
  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails.exception?.description || "?");
    if (msg.method === "Page.frameNavigated" && !msg.params.frame.parentId) loads += 1;
    if (msg.method === "Network.webSocketHandshakeResponseReceived") sockets += 1;
    if (msg.method === "Network.requestWillBeSent" && new URL(msg.params.request.url).pathname === "/health") {
      healthAsked.push(Date.now());
    }
    if (msg.method === "Fetch.requestPaused") {
      const { requestId, request } = msg.params;
      const path = new URL(request.url).pathname;
      if (path === "/health" && healthSays === "fail") {
        send("Fetch.failRequest", { requestId, errorReason: "ConnectionRefused" });
      } else if (path === "/health" && healthSays) {
        const body = Buffer.from(JSON.stringify({ status: "ok", revision: healthSays, version: "0" })).toString("base64");
        send("Fetch.fulfillRequest", { requestId, responseCode: 200, body,
          responseHeaders: [{ name: "content-type", value: "application/json" }, { name: "cache-control", value: "no-store" }] });
      } else if (failPatches && request.method === "PATCH") {
        send("Fetch.failRequest", { requestId, errorReason: "ConnectionRefused" });
      } else {
        send("Fetch.continueRequest", { requestId });
      }
    }
    pending.get(msg.id)?.(msg);
    pending.delete(msg.id);
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Fetch.enable", { patterns: [
    { urlPattern: `${base}/health*`, requestStage: "Request" },
    { urlPattern: `${base}/api/boxes/*`, requestStage: "Request" },
  ] });

  // --- a dev server: revision unknown ---------------------------------------
  await serve("unknown");
  const record = (await api("/boxes", "POST", { content_summary: "kettle and mugs" })).code;
  await send("Page.navigate", { url: `${base}/#/b/${record}/edit` });
  await waitFor(drawn(record), "the record, served by a dev server");
  check("dev: the shell is served unversioned", (await shell()) === "/app.js", await shell());
  let before = loads;
  healthAsked.length = 0;
  await stop();
  await serve("rev-a");
  // Long enough for the socket to reconnect (which would check) and a look.
  await waitFor(`${q("#live")} !== null`, "the page to still be there");
  await sleep(9000);
  await lookAgain();
  await sleep(1500);
  check("dev: a page running revision unknown never reloads", loads === before, `${loads - before} reloads`);
  check("dev: and never even asks /health", healthAsked.length === 0, `${healthAsked.length} asked`);

  // A clean start at rev-a: the dev page's worker would hand back its own
  // unversioned shell, which is a different test (the stale one, below).
  // Page.navigate to the URL already open is only a fragment navigation: a
  // real load is a reload.
  await send("Storage.clearDataForOrigin", { origin: base, storageTypes: "service_workers,cache_storage" });
  await send("Page.reload", { ignoreCache: true });
  await waitFor(drawn(record), "the record at rev-a");
  const worker = `navigator.serviceWorker.getRegistration().then((r) => JSON.stringify(r ? { active: r.active?.state,
    installing: r.installing?.state, waiting: r.waiting?.state, controlled: Boolean(navigator.serviceWorker.controller) } : null))`;
  try {
    await waitFor(`navigator.serviceWorker.controller !== null`, "the service worker to take the page", 20000);
  } catch (error) {
    const tried = await evaluate(`navigator.serviceWorker.register("/sw.js").then(() => "registered", (e) => String(e))`);
    throw new Error(`${error.message}: ${await evaluate(worker)}; register says ${tried}`);
  }
  check("(setup) the page is running rev-a", (await shell()) === "/app.js?v=rev-a", await shell());

  // --- 1. an idle page reloads itself, and lands where it was ---------------
  before = loads;
  await stop();
  await serve("rev-b");
  await until(() => loads > before, "an idle page to reload itself", 45000);
  await waitFor(drawn(record), "the record to draw again");
  check("idle: the page reloaded itself", loads === before + 1, `${loads - before} reloads`);
  check("idle: on the same route", (await evaluate("location.hash")) === `#/b/${record}/edit`, await evaluate("location.hash"));
  check("idle: running the new code, not a shell the worker kept", (await shell()) === "/app.js?v=rev-b", await shell());
  // Checks keep coming -- the reconnect, a look -- and none reloads again.
  await sleep(4000);
  await lookAgain();
  await sleep(2500);
  check("idle: one deploy, one reload", loads === before + 1, `${loads - before} reloads`);
  check("idle: and no banner once it is current", (await banner()) === null, await banner());

  // --- 2. half-typed text is saved first, never reloaded over ---------------
  const words = "three mugs, a milk jug";
  failPatches = true;   // the server cannot take it: a deploy is under way
  await click("#summary-form [name=content_summary]");
  await evaluate(`${q("#summary-form [name=content_summary]")}.select()`);
  await type(words);
  before = loads;
  await stop();
  await serve("rev-c");
  await waitFor(`${q("#live")} && !${q("#live")}.hidden`, "the banner, instead of a reload", 45000);
  const said = await banner();
  check("typing: not reloaded over half-typed text", loads === before, `${loads - before} reloads`);
  check("typing: the banner says the app was updated", /updated/i.test(said || ""), said);
  check("typing: and does not claim another device changed something", !/another device/i.test(said || ""), said);
  check("typing: the field still has the text, and the focus",
        (await evaluate(`${q("#summary-form [name=content_summary]")}.value`)) === words
          && (await evaluate(`document.activeElement?.name`)) === "content_summary");
  check("(setup) the server does not have it yet", (await api(`/boxes/${record}`)).content_summary !== words,
        (await api(`/boxes/${record}`)).content_summary);

  // Tapped while the save still cannot land: it waits rather than losing it,
  // and says what it is waiting for.
  await click("#live");
  await sleep(2500);
  check("typing: a tap while the save cannot land does not reload", loads === before, `${loads - before} reloads`);
  check("typing: the text is still there", (await evaluate(`${q("#summary-form [name=content_summary]")}.value`)) === words);
  check("typing: and the banner says it is waiting for the save", /sav/i.test((await banner()) || ""), await banner());
  // The network comes back -- the page's own `online` path, which retries
  // what failed -- and the reload that was asked for goes ahead once the save
  // has landed, with no second tap.
  failPatches = false;
  await evaluate(`dispatchEvent(new Event("online"))`);
  await until(() => loads > before, "the asked-for reload once the save landed", 45000);
  await waitFor(drawn(record), "the record after that reload");
  check("typing: the text reached the server before the reload",
        (await api(`/boxes/${record}`)).content_summary === words, (await api(`/boxes/${record}`)).content_summary);
  check("typing: and the reloaded page shows it, on the same record",
        (await evaluate(`${q("#summary-form [name=content_summary]")}.value`)) === words
          && (await evaluate("location.hash")) === `#/b/${record}/edit`);
  check("typing: running rev-c", (await shell()) === "/app.js?v=rev-c", await shell());

  // --- 3. an open dialog, camera live, is not reloaded under ----------------
  await send("Browser.grantPermissions", { origin: base, permissions: ["videoCapture"] });
  await click("#add-inside");
  await waitFor(`Boolean(document.querySelector("dialog.adder[open]"))`, "the add-inside dialog");
  const trackState = `document.querySelector("#adder-cam")?.srcObject?.getVideoTracks?.()[0]?.readyState`;
  await waitFor(`${trackState} === "live"`, "the camera to be live");
  before = loads;
  await stop();
  await serve("rev-d");
  await waitFor(`${q("#live")} && !${q("#live")}.hidden`, "the banner behind the dialog", 45000);
  await sleep(2500);
  check("dialog: not reloaded under an open dialog", loads === before, `${loads - before} reloads`);
  check("dialog: which is still open, with the camera still live",
        (await evaluate(`Boolean(document.querySelector("dialog.adder[open]"))`)) && (await evaluate(trackState)) === "live");
  await click("dialog.adder [value=no]");
  await waitFor(`!document.querySelector("dialog[open]")`, "the dialog to close");
  await click("#live");
  await until(() => loads > before, "the reload once the dialog was closed and the banner tapped");
  await waitFor(drawn(record), "the record after that");
  check("dialog: then one tap reloads it, on the same record",
        loads === before + 1 && (await evaluate("location.hash")) === `#/b/${record}/edit`, `${loads - before} reloads`);

  // --- 4. leaving a record is a moment to reload ----------------------------
  const more = " and the teapot";
  await click("#summary-form [name=content_summary]");
  await evaluate(`(() => { const f = ${q("#summary-form [name=content_summary]")}; f.setSelectionRange(f.value.length, f.value.length); })()`);
  await type(more);
  before = loads;
  await stop();
  await serve("rev-e");
  await waitFor(`${q("#live")} && !${q("#live")}.hidden`, "the banner while typing", 45000);
  check("leaving: held while typing", loads === before, `${loads - before} reloads`);
  await click("a.home");
  await until(() => loads > before, "a reload on leaving the record");
  await waitFor(`Boolean(${q("#search")})`, "the list to draw");
  check("leaving: it reloaded once, landing where it was going",
        loads === before + 1 && ["", "#/"].includes(await evaluate("location.hash")), await evaluate("location.hash"));
  check("leaving: the text typed before leaving was saved first",
        (await api(`/boxes/${record}`)).content_summary === words + more, (await api(`/boxes/${record}`)).content_summary);
  check("leaving: running rev-e", (await shell()) === "/app.js?v=rev-e", await shell());

  // --- 5. a /health that fails, and one that keeps disagreeing ---------------
  before = loads;
  healthSays = "fail";
  healthAsked.length = 0;
  await lookAgain();
  await sleep(2500);
  check("health: a check that fails is asked", healthAsked.length > 0, `${healthAsked.length} asked`);
  check("health: and changes nothing -- no reload, no banner", loads === before && (await banner()) === null,
        `${loads - before} reloads, banner ${await banner()}`);

  // Now it says a revision the server never becomes: every reload lands on
  // rev-e and the check still disagrees. One reload, then only an offer.
  healthSays = "rev-never";
  await lookAgain();
  await until(() => loads > before, "the one reload towards rev-never");
  await waitFor(`Boolean(${q("#search")})`, "the list after it");
  for (let i = 0; i < 4; i++) { await sleep(1200); await lookAgain(); }
  await sleep(2000);
  check("loop: a /health that keeps disagreeing costs one reload, not a loop", loads === before + 1, `${loads - before} reloads`);
  check("loop: after that the update is offered, not forced", /updated/i.test((await banner()) || ""), await banner());
  healthSays = null;

  // --- 6. a hidden page does not ask; seen again, it asks at once ------------
  // Headless Chrome keeps its one page visible, so the page is told it is
  // hidden the way the browser would tell it: the property, and the event.
  await evaluate(`(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  })()`);
  await sleep(300);
  before = loads;
  healthAsked.length = 0;
  const socketsBefore = sockets;
  await stop();
  await serve("rev-f");
  await until(() => sockets > socketsBefore, "the hidden page's socket to reconnect", 45000);
  await sleep(2500);
  check("hidden: its socket came back, and it did not ask /health", healthAsked.length === 0, `${healthAsked.length} asked`);
  check("hidden: nor reload", loads === before, `${loads - before} reloads`);
  await evaluate(`(() => {
    delete document.hidden;
    delete document.visibilityState;
    document.dispatchEvent(new Event("visibilitychange"));
  })()`);
  await until(() => loads > before, "a reload on being seen again", 15000);
  await waitFor(`Boolean(${q("#search")})`, "the list after it");
  check("seen again: it asked at once, and reloaded onto rev-f",
        healthAsked.length > 0 && loads === before + 1 && (await shell()) === "/app.js?v=rev-f", `${await shell()}`);

  check("nothing threw in the page", thrown.length === 0, thrown.join(" | "));
} catch (error) {
  check(`harness: ${error.message}`, false);
}

await stop();
const tracebacks = (() => { try { return (readFileSync(join(T, "server.log"), "utf8").match(/Traceback/g) || []).length; } catch { return 0; } })();
check("the server logged no tracebacks", tracebacks === 0, `${tracebacks}`);

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
try { ws?.close(); } catch { /* closed */ }
chrome.kill();
await sleep(300);
for (const dir of [profile, T]) {
  try { rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* the OS will */ }
}
process.exit(failures ? 1 : 0);
