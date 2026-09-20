#!/usr/bin/env node
// Purpose: the record page saves itself as it is edited, with Undo. This
//          drives that for real: REAL key and mouse events (CDP Input.*), so a
//          pause is a pause and leaving a field is a blur the browser fired;
//          every request the page makes is logged at the network layer, bodies
//          included, so "saved exactly once" and "sent the old value" are
//          things it can see; and failures are made at the network layer too
//          (CDP Fetch.failRequest), not by stubbing the page's fetch.
// Date:    2026-09-18
// Usage:   node scripts/claude/autosave_check.mjs [base-url] [--no-vision]
//          default: http://127.0.0.1:8788 (`make run`)
//          CDP_PORT=9340 to move Chrome's debugging port off the default.
//
// THIS ONE WRITES. It makes its own records and edits only those, but it does
// make them, prints (to whatever the server's printer backend is) and uploads
// a photo. So it refuses port 8787 -- the live service, the real database, a
// real printer -- and anything that is not this machine. Point it at a
// throwaway (CLAUDE.md, "A throwaway server is how write paths get checked").
// The background-summary check needs MOVING_VISION_PROVIDER=stub on that
// server; --no-vision skips it rather than wake a real model.
//
// Needs Node 22+ (global WebSocket, zlib.crc32) and Google Chrome. No npm.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { crc32, deflateSync } from "node:zlib";

const args = process.argv.slice(2);
const base = args.find((a) => !a.startsWith("--")) || "http://127.0.0.1:8788";
const vision = !args.includes("--no-vision");
const target = new URL(base);
if (!["127.0.0.1", "localhost", "[::1]"].includes(target.hostname)) {
  console.error(`autosave_check: refusing ${base} -- this check writes, and only ever to a server on this machine.`);
  process.exit(2);
}
if ((target.port || "80") === "8787") {
  console.error("autosave_check: refusing port 8787. That is the live service: the real database,\n" +
    "with labels in circulation, and a real printer. This check creates records, edits them,\n" +
    "prints and uploads. Start a throwaway server on a spare port and point it there.");
  process.exit(2);
}

const PORT = Number(process.env.CDP_PORT) || 9340;
const PAUSE = 1200;   // autosave.js `delay`
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail ?? "")]);
const server = (path, options) => fetch(`${base}${path}`, options).then((r) => r.json());
const post = (path, body) => server(path, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

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
  header[8] = 8; header[9] = 2;   // 8 bits per channel, RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 3).map((_, i) => [r, g, b][i % 3])]);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(Array(size).fill(row)))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const profile = mkdtempSync(join(tmpdir(), "autosave-check-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
   "--window-size=900,1100", "about:blank"], { stdio: "ignore" });

let ws;
try {
  // --- the wire -----------------------------------------------------------------
  let wsUrl;
  for (let i = 0; i < 150 && !wsUrl; i++) {
    try {
      wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json())
        .find((p) => p.type === "page")?.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    if (!wsUrl) await sleep(100);
  }
  if (!wsUrl) throw new Error("Chrome did not start");
  ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

  let id = 0;
  const waiting = new Map();
  const requests = new Map();   // requestId -> { method, url, body, at, failed, status }
  const thrown = [];
  let failing = null;           // { matches(request), left } -- requests to fail at the network
  const send = (method, params = {}) => new Promise((resolve) => {
    waiting.set(++id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.onmessage = (message) => {
    const msg = JSON.parse(message.data);
    if (msg.id) { waiting.get(msg.id)?.(msg); waiting.delete(msg.id); return; }
    const p = msg.params;
    if (msg.method === "Network.requestWillBeSent" && p.request.url.startsWith(base)) {
      requests.set(p.requestId, {
        method: p.request.method, url: p.request.url.slice(base.length),
        body: p.request.postData || "", at: Date.now(), failed: false, status: null });
    } else if (msg.method === "Network.responseReceived") {
      if (requests.has(p.requestId)) requests.get(p.requestId).status = p.response.status;
    } else if (msg.method === "Network.loadingFailed") {
      if (requests.has(p.requestId)) requests.get(p.requestId).failed = true;
    } else if (msg.method === "Runtime.exceptionThrown") {
      thrown.push(p.exceptionDetails.exception?.description || p.exceptionDetails.text);
    } else if (msg.method === "Fetch.requestPaused") {
      const doomed = failing && failing.left > 0 && failing.matches(p.request);
      if (doomed) failing.left -= 1;
      send(doomed ? "Fetch.failRequest" : "Fetch.continueRequest",
           doomed ? { requestId: p.requestId, errorReason: "ConnectionRefused" } : { requestId: p.requestId });
    }
  };
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.exception?.description || "page script failed");
    }
    return r.result.result.value;
  };
  const q = (selector) => `document.querySelector(${JSON.stringify(selector)})`;
  const waitFor = async (expression, what, ms = 8000) => {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (await evaluate(`Boolean(${expression})`)) return true;
      await sleep(50);
    }
    check(`(waiting) ${what}`, false, "never happened");
    return false;
  };

  // Everything the page sent that was not a GET, oldest first.
  const writes = () => [...requests.values()].filter((r) => r.method !== "GET");
  const writesSince = (mark) => writes().slice(mark);
  const show = (list) => list.map((w) => `${w.method} ${w.url} ${w.body}${w.failed ? " (failed)" : ""}`).join(" | ");

  // --- hands ----------------------------------------------------------------------
  async function click(selector) {
    const spot = await evaluate(`(() => { const el = ${q(selector)}; if (!el) return null;
      el.scrollIntoView({ block: "center" }); const b = el.getBoundingClientRect();
      return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
    if (!spot) throw new Error(`nothing to click: ${selector}`);
    for (const type of ["mousePressed", "mouseReleased"]) {
      await send("Input.dispatchMouseEvent", { type, x: spot.x, y: spot.y, button: "left", clickCount: 1 });
    }
    await sleep(60);
  }
  async function type(text, gap = 15) {
    for (const ch of text) {
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: ch, text: ch });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
      await sleep(gap);
    }
  }
  // name -> [virtual key code, text it types, the `key` if it is not the name]
  const KEYS = { Enter: [13, "\r"], Tab: [9, ""], Backspace: [8, ""], ArrowRight: [39, ""], Space: [32, " ", " "] };
  async function press(name, modifiers = 0) {
    const [vk, text, key = name] = KEYS[name];
    await send("Input.dispatchKeyEvent",
      { type: text ? "keyDown" : "rawKeyDown", key, code: name, windowsVirtualKeyCode: vk, text, modifiers });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key, code: name, windowsVirtualKeyCode: vk, modifiers });
    await sleep(40);
  }
  // Click into a text field and put the caret at the end, as a thumb would.
  async function into(selector) {
    await click(selector);
    await evaluate(`(() => { const f = ${q(selector)}; f.setSelectionRange(f.value.length, f.value.length); })()`);
  }
  // A picker is a row of pushbuttons, and unlike the <select> it replaced it
  // can be pressed for real: a mouse press on the button's face, which is the
  // <label> the hidden radio sits in.
  const face = (name, value) => `.seg[data-name="${name}"] input[value="${value}"] + span`;
  const tap = (name, value) => click(face(name, value));
  // (The face eases to its new colour over 90 ms; look once it has arrived.)
  const rowOf = async (name) => { await sleep(160); return lookAtRow(name); };
  const lookAtRow = (name) => evaluate(`(() => { const g = ${q(`.seg[data-name="${name}"]`)}; if (!g) return null;
    const radios = [...g.querySelectorAll("input[type=radio]")];
    return { value: radios.find((r) => r.checked)?.value ?? "", hint: g.querySelector(".seg-said")?.textContent ?? null,
             lit: radios.filter((r) => getComputedStyle(r.nextElementSibling).backgroundColor
                                        === getComputedStyle(r.nextElementSibling).borderTopColor
                                      && getComputedStyle(r.nextElementSibling).backgroundColor !== "rgba(0, 0, 0, 0)").map((r) => r.value),
             pristine: radios.every((r) => r.dataset.initial === String(r.checked)),
             hidden: g.hidden || g.disabled }; })()`);
  const lineOf = (form) => evaluate(`(() => { const l = ${q(`#${form} .autosave`)}; if (!l) return null;
    const u = l.querySelector(".undo");
    return { text: l.querySelector(".autosave-state").textContent, warn: l.classList.contains("warn"),
             undo: u.hidden ? null : u.textContent, height: l.getBoundingClientRect().height,
             undoRight: u.hidden ? null : Math.round(u.getBoundingClientRect().right) }; })()`);
  const valueOf = (name) => evaluate(`(() => { const all = [...document.querySelectorAll('[name="${name}"]')];
    return all[0]?.type === "radio" ? (all.find((r) => r.checked)?.value ?? "") : all[0].value; })()`);
  const kindIs = (kind) => `document.querySelector('[name="kind"]:checked')?.value === ${JSON.stringify(kind)}`;
  const dialogOpen = () => evaluate(`Boolean(document.querySelector("dialog[open]"))`);
  const MARKED = ["summary-form", "destination", "location", "shots", "print"];
  const markPage = () => evaluate(`${JSON.stringify(MARKED)}.forEach((id) => { document.getElementById(id).__mark = id; })`);
  const pageSurvived = () => evaluate(`${JSON.stringify(MARKED)}.every((id) => document.getElementById(id)?.__mark === id)`);
  const patchOf = (w) => { try { return JSON.parse(w.body); } catch { return null; } };

  await send("Runtime.enable");
  await send("Network.enable");
  await send("Page.enable");
  // A headless page does not have the focus, and an unfocused page fires no
  // focus or blur events. Blur is half of how this feature saves.
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  // Chrome has speech recognition and headless Chrome has no microphone. The
  // page's own dictation code runs against this: one phrase, then the end.
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.SpeechRecognition = class { start() { setTimeout(() => {
      this.onresult?.({ results: [[{ transcript: "three mugs" }]] }); this.onend?.(); }, 50); } };` });

  // --- a record of our own ---------------------------------------------------------
  const rooms = await server("/api/rooms");
  const going = rooms.filter((r) => r.kind !== "source");
  const from = rooms.filter((r) => r.kind !== "destination");
  const kinds = await server("/api/settings/kinds");
  if (going.length < 1 || from.length < 1 || kinds.length < 2) {
    throw new Error("needs rooms and kinds to choose between: run `moving seed-rooms` against this database");
  }
  const box = await post("/api/boxes", { content_summary: "pots" });
  const code = box.code;
  for (const name of ["kettle", "toaster"]) await post(`/api/boxes/${code}/items`, { name });
  const api = `/api/boxes/${code}`;
  console.log(`# ${code} on ${base}`);

  await send("Page.navigate", { url: `${base}/#/b/${code}` });
  await waitFor(q("#summary-form"), "the record page");
  await sleep(300);
  const emptyLine = await lineOf("summary-form");

  // === 1. a pause in typing saves, once, and the page does not move ===============
  await markPage();
  await evaluate(`${q("[name=content_summary]")}.__mark = "field"`);
  let mark = writes().length;
  await into("[name=content_summary]");
  await type(" and lids");
  const typedAt = Date.now();
  check("typing says Saving… straight away", (await lineOf("summary-form")).text === "Saving…",
        JSON.stringify(await lineOf("summary-form")));
  await sleep(600);
  check("nothing is sent while the pause is still running", writesSince(mark).length === 0, show(writesSince(mark)));
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Saved"`, "the summary to be saved");
  let sent = writesSince(mark);
  check("a pause saves, exactly once, and only that field",
        sent.length === 1 && sent[0].method === "PATCH" && sent[0].url === api
          && JSON.stringify(patchOf(sent[0])) === JSON.stringify({ content_summary: "pots and lids" }), show(sent));
  check("it waited out the pause before it did", sent[0] && sent[0].at - typedAt >= PAUSE - 100, `${sent[0]?.at - typedAt} ms`);
  const after = await evaluate(`(() => { const f = ${q("[name=content_summary]")};
    return { same: f.__mark === "field", focused: document.activeElement === f, value: f.value,
             caret: f.selectionStart, pristine: f.dataset.initial === f.value }; })()`);
  check("the field is the same element, still focused, text intact, caret at the end",
        after.same && after.focused && after.value === "pots and lids" && after.caret === 13, JSON.stringify(after));
  check("and reads as pristine again, so live updates are not held behind it", after.pristine);
  check("nothing on the page was redrawn", await pageSurvived());
  let line = await lineOf("summary-form");
  check("the line says Saved and offers Undo, named", line.text === "Saved" && line.undo === "Undo summary", JSON.stringify(line));
  check("the line is as tall with Undo on it as it was empty", line.height === emptyLine.height,
        `${emptyLine.height} -> ${line.height}`);
  const savedLine = line;
  await sleep(PAUSE + 600);
  check("it is not saved a second time", writesSince(mark).length === 1, show(writesSince(mark)));

  // typed, and typed back
  mark = writes().length;
  await type(" x");
  await press("Backspace"); await press("Backspace");
  await sleep(PAUSE + 500);
  check("typing something and deleting it again saves nothing", writesSince(mark).length === 0, show(writesSince(mark)));
  // a trailing space is somebody about to type the next word
  await type(" ");
  await sleep(PAUSE + 500);
  check("a trailing space is neither saved nor stripped from under the caret",
        writesSince(mark).length === 0 && (await valueOf("content_summary")) === "pots and lids ", show(writesSince(mark)));

  // === 2. leaving the field saves at once ==========================================
  mark = writes().length;
  await type("and a wok");
  const leftAt = Date.now();
  await press("Tab");
  await sleep(400);
  sent = writesSince(mark);
  check("leaving the field saves at once, not after the pause",
        sent.length === 1 && patchOf(sent[0])?.content_summary === "pots and lids and a wok" && sent[0].at - leftAt < 400,
        `${show(sent)} after ${sent[0]?.at - leftAt} ms`);
  check("Undo is one Tab away from the field", (await evaluate(`document.activeElement?.className`)) === "undo",
        await evaluate(`document.activeElement?.outerHTML.slice(0, 80)`));

  // === 3. a picker saves when it changes ===========================================
  mark = writes().length;
  await tap("destination_room_id", going[0].id);
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Saved"`, "the room to be saved");
  sent = writesSince(mark);
  check("choosing a room saves it, once, as a number",
        sent.length === 1 && JSON.stringify(patchOf(sent[0])) === JSON.stringify({ destination_room_id: going[0].id }), show(sent));
  check("the room band follows without a redraw",
        (await evaluate(`${q("#room-band")}.textContent`)) === going[0].name && (await pageSurvived()),
        await evaluate(`${q("#room-band")}.outerHTML`));
  check("Undo has moved to the form that was just saved in",
        (await lineOf("destination")).undo === "Undo destination room" && (await lineOf("summary-form")).undo === null,
        JSON.stringify([await lineOf("destination"), await lineOf("summary-form")]));
  // The print button reads the page's own copy of the record at click time. It
  // now has contents and a room, so it must print without the thin-label
  // question -- which it only will if autosave kept that copy current.
  mark = writes().length;
  await click("#print");
  await sleep(300);
  check("print sees the autosaved room: no thin-label question", !(await dialogOpen()));
  await waitFor(`!${q("#print")}?.classList.contains("working") && !${q("#print")}?.__mark`, "the print and its redraw");
  check("and prints", writesSince(mark).some((w) => w.url === "/api/labels/print"), show(writesSince(mark)));
  line = await lineOf("destination");
  check("Undo survives the whole-page redraw that follows a print", line.undo === "Undo destination room", JSON.stringify(line));

  // === 4. the location: not on a pause; on leaving; on Return ======================
  await markPage();
  mark = writes().length;
  await into("[name=current_location]");
  await type("garage st");
  await sleep(PAUSE + 900);
  check("the location is NOT saved on a pause", writesSince(mark).length === 0, show(writesSince(mark)));
  check("and says how it will be", /when you leave the field/.test((await lineOf("location")).text),
        (await lineOf("location")).text);
  await type("ack 3");
  await press("Tab");
  await waitFor(`${q("#location .autosave-state")}.textContent === "Saved"`, "the location to be saved");
  sent = writesSince(mark);
  check("leaving it saves it, whole, through the location endpoint",
        sent.length === 1 && sent[0].method === "POST" && sent[0].url === `${api}/location`
          && JSON.stringify(patchOf(sent[0])) === JSON.stringify({ current_location: "garage stack 3" }), show(sent));

  mark = writes().length;
  await into("[name=current_location]");
  await evaluate(`${q("[name=current_location]")}.select()`);
  // Shaped exactly like a scanned box number, Return and all: the wedge
  // listener must leave typing in a field alone.
  await type(code);
  const hashBefore = await evaluate("location.hash");
  await press("Enter");
  await waitFor(`${q("#location .autosave-state")}.textContent === "Saved"`, "Return to save the location");
  sent = writesSince(mark);
  check("Return saves it", sent.length === 1 && patchOf(sent[0])?.current_location === code, show(sent));
  check("Return did not reload the page or lose the focus",
        (await pageSurvived()) && (await evaluate(`document.activeElement?.name`)) === "current_location");
  check("the wedge reader's listener ignored a box number typed into a field",
        (await evaluate("location.hash")) === hashBefore, await evaluate("location.hash"));
  const history = await server(`${api}/events`);
  const places = history.map((e) => JSON.stringify(e)).filter((e) => /garage|B-/.test(e));
  check("the box's history has the two places it was put, and no half-typed one",
        places.some((e) => e.includes("garage stack 3")) && !places.some((e) => /garage st"|garage st[^a]/.test(e)),
        places.join(" "));

  // === 5. Undo sends the old value, and the field shows it =========================
  mark = writes().length;
  await press("Tab");   // off the field, onto Undo
  check("Undo names what it will put back", (await lineOf("location")).undo === "Undo location", JSON.stringify(await lineOf("location")));
  await press("Enter");  // pressed from the keyboard
  await waitFor(`${q("#location .autosave-state")}.textContent === "Undone"`, "the undo to land");
  sent = writesSince(mark);
  check("Undo, pressed with the keyboard, sends the old value",
        sent.length === 1 && patchOf(sent[0])?.current_location === "garage stack 3", show(sent));
  check("and the field shows it, pristine",
        (await valueOf("current_location")) === "garage stack 3"
          && (await evaluate(`${q("[name=current_location]")}.dataset.initial`)) === "garage stack 3");
  check("the server agrees", (await server(api)).current_location === "garage stack 3");

  // === 6. two saves, two undos, in order ===========================================
  // Whatever is on the stack now is older; the two made here must come off first.
  mark = writes().length;
  await into("[name=source_location]");
  await type("shelf 3");
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Saved"`, "where-in-that-room to save on a pause");
  await tap("source_room_id", from[0].id);
  await waitFor(`${q("#destination .undo")}.textContent === "Undo packed from"`, "packed-from to save");
  await click("#destination .undo");
  await waitFor(`${q("#destination .undo")}.textContent === "Undo where in that room"`, "the first undo");
  check("the first Undo puts back the newer save", (await valueOf("source_room_id")) === "" && (await valueOf("source_location")) === "shelf 3",
        `${await valueOf("source_room_id")} / ${await valueOf("source_location")}`);
  await click("#destination .undo");
  await waitFor(`${q("[name=source_location]")}.value === ""`, "the second undo");
  sent = writesSince(mark).map(patchOf);
  check("two saves then two undos walk back newest first",
        JSON.stringify(sent) === JSON.stringify([{ source_location: "shelf 3" }, { source_room_id: from[0].id },
                                                 { source_room_id: null }, { source_location: null }]),
        JSON.stringify(sent));
  const walked = await server(api);
  check("and the server is back where it started", walked.source_room_id === null && walked.source_location === null);
  check("the stack goes on down to older saves", (await lineOf("destination")).undo === "Undo location",
        JSON.stringify(await lineOf("destination")));

  // Undo pressed straight after typing, inside the pause: it must take back
  // the typing -- not race the save, and not undo the older save beneath it.
  mark = writes().length;
  await into("[name=content_summary]");
  await type(" zzz");
  check("while an edit is on its way, Undo already names it", (await lineOf("summary-form")).undo === "Undo summary",
        JSON.stringify(await lineOf("summary-form")));
  await click("#summary-form .undo");
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Undone"`, "the undo of the typing");
  sent = writesSince(mark).map(patchOf);
  check("Undo inside the pause saves the typing first, then takes it back, in that order",
        JSON.stringify(sent) === JSON.stringify([{ content_summary: "pots and lids and a wok zzz" },
                                                 { content_summary: "pots and lids and a wok" }]), JSON.stringify(sent));
  check("and the field shows what it was before the typing", (await valueOf("content_summary")) === "pots and lids and a wok",
        await valueOf("content_summary"));

  // === 7. a save that fails: no dialog, the edit kept, retried =====================
  await send("Fetch.enable", { patterns: [{ urlPattern: `${base}/api/boxes/*`, requestStage: "Request" }] });
  const summaryPatch = (request) => request.method === "PATCH" && (request.postData || "").includes("content_summary");

  failing = { matches: summaryPatch, left: 1 };
  mark = writes().length;
  await markPage();
  await into("[name=content_summary]");
  await type(", colander");
  await waitFor(`${q("#summary-form .autosave")}.classList.contains("warn")`, "the failure to show");
  line = await lineOf("summary-form");
  check("a failed save says so on the line, as a warning", line.text === "Not saved yet — will retry" && line.warn, JSON.stringify(line));
  check("no dialog, while somebody is typing", !(await dialogOpen()));
  check("the edit is kept, the caret is still in it, nothing was redrawn",
        (await valueOf("content_summary")) === "pots and lids and a wok, colander"
          && (await evaluate(`document.activeElement?.name`)) === "content_summary" && (await pageSurvived()));
  check("the failed line is no taller than the empty one", line.height === emptyLine.height, `${line.height}`);
  // Pressing Undo blurs the field, which changes the text. If that moved the
  // button, the press and the release would land on different things.
  check("Undo stays where it was when the text beside it changes",
        line.undo !== null && line.undoRight === savedLine.undoRight, `${savedLine.undoRight} -> ${line.undoRight}`);
  check("the request really did fail at the network", writesSince(mark).length === 1 && writesSince(mark)[0].failed, show(writesSince(mark)));
  await press("Tab");   // leaving the field commits: the retry
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Saved"`, "the retry to land");
  sent = writesSince(mark);
  check("leaving the field retries, and this time it lands",
        sent.length === 2 && !sent[1].failed && patchOf(sent[1])?.content_summary === "pots and lids and a wok, colander", show(sent));

  // ...and with nobody touching anything
  failing = { matches: summaryPatch, left: 1 };
  mark = writes().length;
  await into("[name=content_summary]");
  await type(", sieve");
  const failedAt = Date.now();
  await waitFor(`${q("#summary-form .autosave")}.classList.contains("warn")`, "the second failure");
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Saved"`, "the automatic retry", 6000);
  sent = writesSince(mark);
  check("left alone, it retries by itself a couple of seconds later",
        sent.length === 2 && sent[0].failed && !sent[1].failed && sent[1].at - sent[0].at >= 1800,
        `${show(sent)}; retried after ${sent[1]?.at - sent[0]?.at} ms (${Date.now() - failedAt} ms in all)`);
  check("still focused, still no dialog", (await evaluate(`document.activeElement?.name`)) === "content_summary" && !(await dialogOpen()));

  // ...and across a whole-page redraw: the unsaved text is carried over
  failing = { matches: summaryPatch, left: 2 };
  mark = writes().length;
  await type(", whisk");
  await click("[data-flag=fragile]");   // blur -> commit (fails); the chip saves and redraws the page
  await waitFor(`!${q("#summary-form")}.__mark`, "the chip's redraw");
  await sleep(200);
  line = await lineOf("summary-form");
  check("text the server never got survives a whole-page redraw",
        (await valueOf("content_summary")) === "pots and lids and a wok, colander, sieve, whisk", await valueOf("content_summary"));
  check("and the new page still says it is not saved", line.warn && line.text === "Not saved yet — will retry", JSON.stringify(line));
  check("the chip itself was saved", (await server(api)).fragile === 1);
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Saved"`, "the carried-over edit to land", 12000);
  check("it lands once the network lets it", (await server(api)).content_summary === "pots and lids and a wok, colander, sieve, whisk",
        (await server(api)).content_summary);

  // Undo beside "Not saved yet": what it takes back is the edit that never
  // arrived -- one step, and nothing to send -- not the save beneath it.
  const kept = (await server(api)).content_summary;
  failing = { matches: summaryPatch, left: 99 };
  mark = writes().length;
  await into("[name=content_summary]");
  await type(", never sent");
  await waitFor(`${q("#summary-form .autosave")}.classList.contains("warn")`, "the third failure");
  check("Undo is offered beside a failure", (await lineOf("summary-form")).undo === "Undo summary", JSON.stringify(await lineOf("summary-form")));
  await click("#summary-form .undo");
  await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Undone"`, "the unsaved edit to be given up");
  check("Undo on a failed edit gives up that edit: one step back, not two",
        (await valueOf("content_summary")) === kept && (await server(api)).content_summary === kept,
        `${await valueOf("content_summary")} / ${(await server(api)).content_summary}`);
  check("everything it sent was a last try at the save, none of which landed",
        writesSince(mark).every((w) => w.failed && patchOf(w)?.content_summary?.endsWith("never sent")), show(writesSince(mark)));
  check("the field is pristine again and the warning is gone",
        (await evaluate(`(() => { const f = ${q("[name=content_summary]")}; return f.dataset.initial === f.value; })()`))
          && !(await lineOf("summary-form")).warn);
  failing = null;
  await sleep(2500);   // a retry left behind would fire about now
  check("no retry was left behind to send it after all", (await server(api)).content_summary === kept);
  await send("Fetch.disable");

  // === 8. From contents ==========================================================
  const beforeSuggest = await valueOf("content_summary");
  mark = writes().length;
  await click("#suggest");
  await waitFor(`${q("#summary-form .undo")} && !${q("#summary-form .undo")}.hidden && ${q("[name=content_summary]")}.value !== ${JSON.stringify(beforeSuggest)}`,
                "From contents to fill and save");
  sent = writesSince(mark);
  check("From contents saves what it wrote, at once", sent.length === 1 && patchOf(sent[0])?.content_summary === "kettle, toaster", show(sent));
  await click("#summary-form .undo");
  await waitFor(`${q("[name=content_summary]")}.value === ${JSON.stringify(beforeSuggest)}`, "Undo to put the old summary back");
  check("Undo puts the old summary back, here and on the server",
        (await server(api)).content_summary === beforeSuggest, (await server(api)).content_summary);

  // === 9. dictation feeds the same saver; a redraw inside the pause commits =========
  if (await evaluate(`Boolean(${q("#summary-form .dictate")}) && !${q("#summary-form .dictate")}.hidden`)) {
    mark = writes().length;
    await click("#summary-form .dictate");
    await waitFor(`${q("[name=content_summary]")}.value.endsWith("three mugs")`, "the dictated phrase");
    await sleep(PAUSE + 600);
    sent = writesSince(mark);
    check("dictated text is saved after the pause like typed text",
          sent.length === 1 && patchOf(sent[0])?.content_summary === `${beforeSuggest}, three mugs`, show(sent));

    // Nothing has the focus this time, so no blur will commit it: the only
    // thing between this edit and a redraw 100 ms later is viewBox.
    mark = writes().length;
    await click("#summary-form .dictate");
    await waitFor(`${q("[name=content_summary]")}.value.endsWith("three mugs, three mugs")`, "the second phrase");
    const dictatedAt = Date.now();
    await evaluate(`document.querySelector("[data-flag=heavy]").click()`);
    await waitFor(`${q("[data-flag=heavy]")}?.classList.contains("on")`, "the chip's redraw");
    await sleep(300);
    sent = writesSince(mark).filter((w) => (w.body || "").includes("content_summary"));
    check("a redraw inside the pause commits the edit first, rather than dropping it",
          sent.length === 1 && sent[0].at - dictatedAt < PAUSE - 200 && patchOf(sent[0]).content_summary.endsWith("three mugs, three mugs"),
          `${show(sent)} after ${sent[0]?.at - dictatedAt} ms`);
    check("and the redrawn field has it", (await valueOf("content_summary")).endsWith("three mugs, three mugs"), await valueOf("content_summary"));
  } else {
    check("the Dictate button is wired up where the browser can dictate", false, "button missing or hidden");
  }

  // === 9b. rows of pushbuttons: a second tap clears, and Undo lights it again =======
  await markPage();
  let row = await rowOf("destination_room_id");
  check("(setup) the destination row shows the room chosen earlier", row.value === String(going[0].id) && row.hint === "Tap it again to clear", JSON.stringify(row));
  mark = writes().length;
  await tap("destination_room_id", going[0].id);   // the selected one, again
  await waitFor(`${q("#room-band")}.hidden`, "the room to be cleared");
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Saved"`, "the clearing to be saved");
  sent = writesSince(mark);
  check("a second tap on the selected button clears it, and saves null, once",
        sent.length === 1 && JSON.stringify(patchOf(sent[0])) === JSON.stringify({ destination_room_id: null }), show(sent));
  row = await rowOf("destination_room_id");
  check("nothing in the row is lit, and the line under it says what that means",
        row.value === "" && row.lit.length === 0 && row.hint === "Not decided yet", JSON.stringify(row));
  check("the cleared row is pristine, and the page was not redrawn", row.pristine && (await pageSurvived()));
  check("the server has no room", (await server(api)).destination_room_id === null);

  mark = writes().length;
  await click("#destination .undo");
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Undone"`, "the undo of the clearing");
  row = await rowOf("destination_room_id");
  check("Undo sends the room back", JSON.stringify(writesSince(mark).map(patchOf)) === JSON.stringify([{ destination_room_id: going[0].id }]), show(writesSince(mark)));
  check("and the right button is lit again, visibly", row.value === String(going[0].id) && JSON.stringify(row.lit) === JSON.stringify([String(going[0].id)]), JSON.stringify(row));
  check("with the band, the hint and the baseline to match",
        (await evaluate(`${q("#room-band")}.textContent`)) === going[0].name && row.hint === "Tap it again to clear" && row.pristine, JSON.stringify(row));

  // a row that must have a value
  mark = writes().length;
  await tap("kind", "box");
  await sleep(500);
  check("a second tap on the kind does nothing: a record is always something",
        writesSince(mark).length === 0 && (await valueOf("kind")) === "box" && (await pageSurvived()), show(writesSince(mark)));

  // size: a container has one to choose
  row = await rowOf("size");
  check("a box has a size row, with nothing chosen and saying so", row && row.value === "" && row.hint === "No size", JSON.stringify(row));
  mark = writes().length;
  await tap("size", "large");
  await waitFor(`${q("#destination .undo")}.textContent === "Undo size"`, "the size to be saved");
  check("choosing a size saves it, once", JSON.stringify(writesSince(mark).map(patchOf)) === JSON.stringify([{ size: "large" }]), show(writesSince(mark)));
  // ...from the keyboard: an arrow moves the choice, Space on it clears
  await evaluate(`document.querySelector('.seg[data-name="size"] input:checked').focus()`);
  mark = writes().length;
  await press("ArrowRight");
  await waitFor(`document.querySelector('[name="size"]:checked')?.value === "extra large"`, "the arrow key to move the choice");
  await sleep(300);
  check("an arrow key moves the choice and saves it", JSON.stringify(writesSince(mark).map(patchOf)) === JSON.stringify([{ size: "extra large" }]), show(writesSince(mark)));
  mark = writes().length;
  await press("Space");
  await waitFor(`!document.querySelector('[name="size"]:checked')`, "Space to clear the size");
  await sleep(300);
  check("Space on the chosen button clears it, and saves null", JSON.stringify(writesSince(mark).map(patchOf)) === JSON.stringify([{ size: null }]), show(writesSince(mark)));
  check("the keyboard's way of clearing is said to a screen reader",
        await evaluate(`(() => { const r = document.querySelector('[name="size"]'); const d = document.getElementById(r.getAttribute("aria-describedby"));
          document.querySelector('[name="size"][value="small"]').click(); return /press Space/.test(d.textContent) && d.querySelector(".vh") !== null; })()`));
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Saved"`, "small to be saved");
  await tap("size", "large");
  await waitFor(`${q("#destination .autosave-state")}.textContent === "Saved" && document.querySelector('[name="size"]:checked')?.value === "large"`, "large to be saved");
  check("the server has the size", (await server(api)).size === "large", (await server(api)).size);

  // the list says it
  await evaluate(`location.hash = "#/"`);
  await waitFor(q(`#boxlist li[data-key="${code}"]`), "the list");
  check("the list row says large box", (await evaluate(`${q(`#boxlist li[data-key="${code}"] .k`)}.textContent`)) === "large box",
        await evaluate(`${q(`#boxlist li[data-key="${code}"] .w`)}.textContent`));
  await evaluate(`location.hash = ${JSON.stringify(`#/b/${code}`)}`);
  await waitFor(q('.seg[data-name="size"]'), "the record again");
  await sleep(300);
  check("a box's label count is its kind's", (await evaluate(`${q("#copies")}.value`)) === String(kinds.find((k) => k.kind === "box").copies));

  // === 10. kind: the one save that redraws, and its Undo ===========================
  const loose = kinds.find((k) => !k.contents) || kinds.find((k) => k.kind !== "box");
  mark = writes().length;
  await markPage();
  await tap("kind", loose.kind);
  await waitFor(`!${q("#summary-form")}.__mark && ${kindIs(loose.kind)}`, "the kind's redraw");
  await sleep(200);
  sent = writesSince(mark);
  check("changing the kind saves it", sent.length === 1 && JSON.stringify(patchOf(sent[0])) === JSON.stringify({ kind: loose.kind }), show(sent));
  check("and redraws the page for the new kind", Boolean(await evaluate(`${q("#items")}`)) === Boolean(loose.contents));
  check("the chosen button has the focus back after the redraw",
        await evaluate(`document.activeElement?.name === "kind" && document.activeElement.checked`));
  check("a single thing has no size row at all", (await rowOf("size")) === null);
  check("and the server took its size away", (await server(api)).size === null, (await server(api)).size);
  check("its label count follows the kind too", (await evaluate(`${q("#copies")}.value`)) === String(loose.copies), await evaluate(`${q("#copies")}.value`));
  line = await lineOf("destination");
  check("Undo kind is offered on the new page", line.undo === "Undo kind", JSON.stringify(line));
  await markPage();
  await click("#destination .undo");
  await waitFor(`!${q("#summary-form")}.__mark && ${kindIs("box")}`, "the undo's redraw");
  check("Undo restores the kind, redraws, and the server agrees", (await server(api)).kind === "box" && Boolean(await evaluate(`${q("#items")}`)));
  row = await rowOf("size");
  check("the size row is back, showing the server's truth: the size went with the kind",
        row && row.value === "" && row.hint === "No size" && row.pristine, JSON.stringify(row));

  // === 11. the copies field is none of autosave's business ==========================
  mark = writes().length;
  await click("#copies");   // a number field has no caret to place
  await evaluate(`${q("#copies")}.select()`);
  await type("3");
  await press("Tab");
  await sleep(PAUSE + 500);
  check("the copies field is not saved anywhere", writesSince(mark).length === 0, show(writesSince(mark)));
  check("and does not make the page look half-edited", (await evaluate(`${q("#copies")}.dataset.initial`)) === "3");

  // === 12. going away inside the pause =============================================
  mark = writes().length;
  await into("[name=source_location]");
  await type("under the stairs");
  const awayAt = Date.now();
  await evaluate(`location.hash = "#/"`);   // no blur: the field is simply gone
  await waitFor(q("#search"), "the list");
  await sleep(300);
  sent = writesSince(mark);
  // Timed, because the saver outlives the page: left alone its pause would
  // still run out and send this 1.2 s later, which is not the same promise.
  check("navigating away inside the pause saves there and then",
        sent.length === 1 && patchOf(sent[0])?.source_location === "under the stairs" && sent[0].at - awayAt < 400,
        `${show(sent)} after ${sent[0]?.at - awayAt} ms`);
  check("the server has it", (await server(api)).source_location === "under the stairs");

  await evaluate(`location.hash = ${JSON.stringify(`#/b/${code}`)}`);
  await waitFor(q("#summary-form"), "the record again");
  await sleep(300);
  check("coming back starts afresh: no Undo from the earlier visit",
        (await evaluate(`Array.from(document.querySelectorAll(".autosave .undo")).every((u) => u.hidden)`)));

  // The screen going dark. document.hidden cannot be driven from here, so the
  // property is overridden and the event fired: the page's listener is real.
  mark = writes().length;
  await into("[name=source_location]");
  await type(", left");
  const darkAt = Date.now();
  await evaluate(`(() => { Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange")); })()`);
  await sleep(300);
  sent = writesSince(mark);
  check("the screen going dark inside the pause saves there and then (synthetic visibilitychange)",
        sent.length === 1 && patchOf(sent[0])?.source_location === "under the stairs, left" && sent[0].at - darkAt < 400,
        `${show(sent)} after ${sent[0]?.at - darkAt} ms`);
  await evaluate(`(() => { delete document.hidden; document.dispatchEvent(new Event("visibilitychange")); })()`);

  // === 12b. the new-record form: the same rows, and what it sends ======================
  const create = async (steps) => {
    await evaluate(`location.hash = "#/new"`);
    await waitFor(q('#new .seg[data-name="kind"]'), "the new-record form");
    await sleep(200);
    await steps();
    const at = writes().length;
    await click("#create");
    await waitFor(`location.hash.startsWith("#/b/")`, "the record to be created");
    return patchOf(writesSince(at).find((w) => w.method === "POST" && w.url === "/api/boxes"));
  };
  let made = await create(async () => {
    await tap("kind", "crate");
    check("new form: Create names the kind chosen", (await evaluate(`${q("#create")}.textContent`)) === "Create crate");
    await tap("size", "medium");
    await tap("destination_room_id", going[0].id);
    await tap("source_room_id", from[0].id);
    await into("#new [name=content_summary]");
    await type("winter coats");
  });
  check("new form: it sends the kind, size and rooms chosen",
        made && made.kind === "crate" && made.size === "medium" && made.destination_room_id === going[0].id
          && made.source_room_id === from[0].id && made.content_summary === "winter coats", JSON.stringify(made));
  made = await create(async () => {
    await tap("size", "small");               // chosen while it is still a box...
    await tap("destination_room_id", going[0].id);
    await tap("destination_room_id", going[0].id);   // ...and a room chosen, then cleared
    await tap("kind", "furniture");
    row = await rowOf("size");
    check("new form: a single thing has no size row", row.hidden, JSON.stringify(row));
  });
  check("new form: a hidden size is not sent, nor a cleared room",
        made && made.kind === "furniture" && !("size" in made) && !("destination_room_id" in made), JSON.stringify(made));
  check("new form: and the server made it", (await server(`/api/boxes/${(await evaluate("location.hash")).slice(4)}`)).kind === "furniture");

  // === 13. the server rewrites a pristine summary: not an edit to save back =========
  if (vision) {
    const quiet = await post("/api/boxes", { content_summary: "" });
    await evaluate(`location.hash = ${JSON.stringify(`#/b/${quiet.code}`)}`);
    await waitFor(`${q("h1.code")}?.textContent === ${JSON.stringify(quiet.code)}`, "the second record");
    await sleep(500);
    await evaluate(`${q("[name=content_summary]")}.__mark = "field"`);
    mark = writes().length;
    const body = new FormData();
    body.append("file", new Blob([png(96, [180, 150, 110])], { type: "image/png" }), "check.png");
    await fetch(`${base}/api/boxes/${quiet.code}/photos`, { method: "POST", body });
    const filled = await waitFor(`${q("[name=content_summary]")}.value !== ""`,
      "the background worker to write the summary (needs MOVING_VISION_PROVIDER=stub)", 25000);
    if (filled) {
      const arrived = await valueOf("content_summary");
      await sleep(PAUSE + 1200);
      check("a summary written by the background worker lands in the same, pristine field",
            await evaluate(`(() => { const f = ${q("[name=content_summary]")}; return f.__mark === "field" && f.dataset.initial === f.value; })()`),
            arrived);
      check("and is NOT saved back as if somebody had typed it", writesSince(mark).length === 0, show(writesSince(mark)));
      check("so it is still the server's to rewrite", (await server(`/api/boxes/${quiet.code}`)).summary_source === "auto",
            (await server(`/api/boxes/${quiet.code}`)).summary_source);
      line = await lineOf("summary-form");
      check("the line does not claim a save that was not one", line.text === "" && line.undo === null, JSON.stringify(line));
      // ...and it is a baseline like any other: an edit on top of it saves, and undoes to it
      await into("[name=content_summary]");
      await type(" and a jug");
      await waitFor(`${q("#summary-form .autosave-state")}.textContent === "Saved"`, "the edit on top to save");
      await click("#summary-form .undo");
      await waitFor(`${q("[name=content_summary]")}.value === ${JSON.stringify(arrived)}`, "Undo back to the worker's summary");
      check("an edit on top of it undoes back to it", (await server(`/api/boxes/${quiet.code}`)).content_summary === arrived);
    }
  }

  // === 14. closing the tab inside the pause ========================================
  await evaluate(`location.hash = ${JSON.stringify(`#/b/${code}`)}`);
  await waitFor(`${q("h1.code")}?.textContent === ${JSON.stringify(code)}`, "the first record again");
  await sleep(400);
  await into("[name=source_location]");
  await type(", by the door");
  await send("Page.navigate", { url: "about:blank" });   // pagehide, with the pause still running
  await sleep(1200);
  check("leaving the site inside the pause still saves (pagehide + keepalive)",
        (await server(api)).source_location === "under the stairs, left, by the door", (await server(api)).source_location);

  check("nothing threw in the page at any point", thrown.length === 0, thrown.join(" | "));
} catch (error) {
  check(`harness: ${error.message}`, false, error.stack?.split("\n")[1] || "");
}

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed of ${results.length}` : `\nall ${results.length} passed`);
try { ws?.close(); } catch { /* never opened */ }
chrome.kill();
await sleep(300);
// Chrome can still be letting go of its profile; a leftover temp directory is
// not worth turning a green run red for.
try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* the OS will */ }
process.exit(failures ? 1 : 0);
