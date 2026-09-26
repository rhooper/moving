#!/usr/bin/env node
// Purpose: prove that a regenerated strip image is actually FETCHED by a phone
//          that already has the old one, and that rewriting an id-keyed URL's
//          bytes in place is not. The service worker answers photos from its
//          own cache and photos are cached for a year, so a URL whose bytes
//          change is never fetched again; the strip's URL carries a version
//          derived from the recipe.
//
//          One Chrome, one service worker, one set of caches, across two
//          servers on the same origin: the recipe as shipped, then a changed
//          recipe shipped with a deploy -- a new revision, a new sw.js, the old
//          worker's cache wiped. What can go stale then is the HTTP cache
//          beneath it.
//
//          The control rewrites the list thumbnail in place under its
//          unchanged URL and must see it stay stale; without that a pass would
//          prove nothing.
// Date:    2026-09-21
// Usage:   node scripts/claude/strip_cache_check.mjs
//          Its own throwaway database and photos; never the live service.
//          PORT (default 8807) and CDP_PORT (default 9374) keep runs apart.
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const NAME = "strip_cache_check";
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PORT = Number(process.env.PORT) || 8807;
const CDP = Number(process.env.CDP_PORT) || 9374;
if (PORT === 8787) {
  console.error(`${NAME}: refusing port 8787 -- that is the live service.`);
  process.exit(2);
}
const BASE = `http://127.0.0.1:${PORT}`;
const T = mkdtempSync(join(tmpdir(), "strip-cache-"));
const env = {
  ...process.env,
  MOVING_DB_PATH: `${T}/t.db`, MOVING_PHOTO_DIR: `${T}/photos`,
  MOVING_LABEL_PREVIEW_DIR: `${T}/prev`, MOVING_BACKUP_DIR: `${T}/bk`,
  MOVING_PRINTER_BACKEND: "fake", MOVING_AUTO_ANALYSE: "0", MOVING_PHRASE_SUMMARIES: "0",
  MOVING_VISION_PROVIDER: "stub", MOVING_ENV_FILE: `${T}/no.env`,
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha = (buf) => createHash("sha256").update(buf).digest("hex").slice(0, 16);
const py = (code) => execFileSync("uv", ["run", "python", "-c", code], { cwd: ROOT, env }).toString().trim();

// A server whose strip recipe can be changed, standing in for the deploy that
// would ship a retuned one. The app itself has no such knob.
const SERVE = `
import sys, uvicorn
from movingbox import renditions
from movingbox.api.app import create_app
from movingbox.config import from_env
if len(sys.argv) > 2:
    renditions.UNSHARP = (float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    renditions.VERSION = renditions.version(unsharp=renditions.UNSHARP)
print("version", renditions.VERSION, flush=True)
uvicorn.run(create_app(from_env()), host="127.0.0.1", port=int(sys.argv[1]))
`;
let log = "";
function startServer(revision, recipe = []) {
  log = "";
  const child = spawn("uv", ["run", "python", "-c", SERVE, String(PORT), ...recipe],
    { cwd: ROOT, env: { ...env, MOVING_REVISION: revision }, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout.on("data", (d) => (log += d));
  child.stderr.on("data", (d) => (log += d));
  return child;
}
async function up() {
  for (let i = 0; i < 150; i++) { try { if ((await fetch(`${BASE}/health`)).ok) return; } catch {} await sleep(150); }
  throw new Error(`server never came up:\n${log}`);
}
async function down(child) {
  child.kill();
  for (let i = 0; i < 80; i++) { try { await fetch(`${BASE}/health`); } catch { return; } await sleep(100); }
}

const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${detail ? `\n        ${detail}` : ""}`);
};

// Pre-migrate: a brand-new database can 500 once when a page's first two
// requests race to create the schema.
execFileSync("uv", ["run", "moving", "seed-rooms"], { cwd: ROOT, env, stdio: "ignore" });

const profile = mkdtempSync(join(tmpdir(), "strip-cache-chrome-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", `--remote-debugging-port=${CDP}`, `--user-data-dir=${profile}`,
   "about:blank"], { stdio: "ignore" });
let wsUrl;
for (let i = 0; i < 150 && !wsUrl; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${CDP}/json`)).json()).find((p) => p.type === "page")?.webSocketDebuggerUrl; } catch {}
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
const waitFor = async (expression, what, tries = 250) => {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return; await sleep(100); }
  throw new Error(`timed out: ${what}`);
};
// What the PAGE gets for a URL: through the service worker, as an <img> would.
const pageSees = (url) => evaluate(`(async () => {
  const buf = await (await fetch(${JSON.stringify(url)})).arrayBuffer();
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
})()`);
const inWorkerCache = (url) => evaluate(`caches.match(${JSON.stringify(url)}).then(Boolean)`);
const stripOnPage = () => evaluate(`(async () => {
  const img = document.querySelector(".shots img");
  img.loading = "eager"; await img.decode().catch(() => {});
  return img.currentSrc.replace(location.origin, "");
})()`);

let server;
let failed = true;
try {
  await send("Page.enable"); await send("Runtime.enable");
  // The phone: a high-DPI Android at its usual width.
  await send("Emulation.setDeviceMetricsOverride", { width: 412, height: 915, deviceScaleFactor: 3, mobile: true });

  // ---- phase A: the recipe as shipped ----------------------------------------
  server = startServer("proof-a"); await up();
  const versionA = log.match(/version (\w+)/)[1];
  const code = (await (await fetch(`${BASE}/api/boxes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json()).code;
  const jpeg = `${T}/shot.jpg`;
  py(`from PIL import Image, ImageDraw
im = Image.new("RGB", (1536, 2048), (200, 170, 130)); d = ImageDraw.Draw(im)
[d.rectangle([x, 300, x + 8, 1700], fill=(20, 20, 20)) for x in range(100, 1400, 24)]
im.save(${JSON.stringify(jpeg)}, quality=92)`);
  const form = new FormData();
  form.append("file", new Blob([readFileSync(jpeg)], { type: "image/jpeg" }), "shot.jpg");
  const photo = await (await fetch(`${BASE}/api/boxes/${code}/photos`, { method: "POST", body: form })).json();
  const thumbUrl = `/photos/${photo.id}/thumb?k=${photo.key}`;
  console.log(`phase A: revision proof-a, strip version ${versionA}, ${code} photo ${photo.id}`);

  await send("Page.navigate", { url: `${BASE}/#/` });
  await waitFor(`navigator.serviceWorker && navigator.serviceWorker.controller !== null`, "the service worker to take control");
  // Controlled now: from here every image goes through it.
  await send("Page.reload");
  await waitFor(`[...document.querySelectorAll("main img")].some((i) => i.currentSrc.includes(${JSON.stringify(thumbUrl)}) && i.complete)`, "the list's cover");
  await send("Page.navigate", { url: `${BASE}/#/b/${code}/edit` });
  await waitFor(`document.querySelectorAll(".shots img").length > 0`, "the strip");
  const stripA = await stripOnPage();
  const stripSeenA = await pageSees(stripA);
  const thumbSeenA = await pageSees(thumbUrl);
  check("a 3x phone asks for the versioned strip, not the thumbnail",
    stripA === `/photos/${photo.id}/strip?v=${versionA}&k=${photo.key}`, stripA);
  check("the service worker has cached the strip", await inWorkerCache(stripA));
  check("...and the list thumbnail", await inWorkerCache(thumbUrl));

  // ---- between: a retuned recipe ships with a deploy; and the CONTROL -------
  // rewrites the list thumbnail in place under its unchanged, id-keyed URL.
  await down(server);
  const thumbPath = `${T}/photos/${photo.thumb_filename}`;
  py(`from PIL import Image; Image.new("RGB", (300, 400), (220, 30, 30)).save(${JSON.stringify(thumbPath)}, "JPEG")`);
  const thumbOnDisk = sha(readFileSync(thumbPath));

  // ---- phase B: new revision, changed recipe, same browser and caches --------
  server = startServer("proof-b", ["1.0", "120", "3"]); await up();
  const versionB = log.match(/version (\w+)/)[1];
  console.log(`\nphase B: revision proof-b, strip version ${versionB}`);
  await send("Page.navigate", { url: `${BASE}/#/b/${code}/edit` });
  // The deploy's new worker installs, activates and deletes the old cache.
  // Wait until that has really happened, or this proves nothing about it.
  await waitFor(`caches.keys().then((k) => k.length === 1 && k[0] === "proof-b")`,
    "the new service worker to replace the old one's cache");
  check("the deploy's new worker wiped the old one's cache", !(await inWorkerCache(stripA)));
  await send("Page.reload");
  await waitFor(`document.querySelectorAll(".shots img").length > 0`, "the strip again");
  const stripB = await stripOnPage();
  const stripSeenB = await pageSees(stripB);
  const stripOnDisk = sha(readFileSync(`${T}/photos/${photo.filename.replace(".jpg", `-strip-${versionB}.jpg`)}`));
  const thumbSeenB = await pageSees(thumbUrl);

  // Compared with phase A's URL, not only with the version the server reports:
  // a version that ignored the recipe would report the same one and pass.
  check("the page names a DIFFERENT strip URL",
    stripB !== stripA && stripB === `/photos/${photo.id}/strip?v=${versionB}&k=${photo.key}`, `${stripA} -> ${stripB}`);
  check("the server was actually asked for it", log.includes(`GET ${stripB} HTTP`),
    "in the phase-B server's access log -- made on demand, since nothing had made it yet");
  check("the page sees the regenerated bytes", stripSeenB === stripOnDisk && stripSeenB !== stripSeenA,
    `page ${stripSeenB}, on disk ${stripOnDisk}; phase A was ${stripSeenA}`);
  check("CONTROL: an id-keyed URL rewritten in place stays stale, even across the deploy",
    thumbSeenB === thumbSeenA && thumbSeenB !== thumbOnDisk,
    `the page still sees ${thumbSeenB} (as in phase A); the file on disk is now ${thumbOnDisk}.` +
    " The worker's cache is gone, so this is the HTTP cache answering.");
  failed = results.some((ok) => !ok);
} catch (error) {
  console.error(`${NAME}: ${error.message}`);
} finally {
  if (server) await down(server);
  ws.close(); chrome.kill();
  await sleep(300);
  rmSync(T, { recursive: true, force: true });
  rmSync(profile, { recursive: true, force: true });
}
// The house summary line, which browser_checks.sh reads to report a run.
const passed = results.filter(Boolean).length;
console.log(passed === results.length ? `\nall ${passed} passed` : `\n${results.length - passed} failed`);
process.exit(failed ? 1 : 0);
