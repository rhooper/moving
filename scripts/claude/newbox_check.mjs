#!/usr/bin/env node
// Purpose: the live camera on the new-record page, pressed for real on a
//          synthetic webcam. The viewfinder comes up by itself; the shutter
//          keeps a frame and offers another; Create makes the record and the
//          photograph lands on it; **creating with no photo is untouched** --
//          the camera never stands between anyone and the buttons; a refused
//          camera leaves an honest line and a working file picker; Enter still
//          reaches the plain Create, which is now the first button and prints
//          nothing; and the camera is let go when the page is left, by
//          whatever means, which a page has no `close` event to do for it.
//          Then the record sheet's camera, beside Edit: the same field in a
//          dialog -- it opens live, each press of the shutter adds a photo at
//          once and brings the viewfinder back, the sheet shows them in place,
//          Done and Escape with nothing taken send nothing, and a refused
//          camera says so there too.
// Date:    2026-09-22
// Usage:   node scripts/claude/newbox_check.mjs <base-url>
//          WRITES: creates records. Refuses the live service.
//          scripts/claude/browser_checks.sh runs it on a throwaway.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const NAME = "newbox_check";
{
  const target = new URL(process.argv[2] || "http://127.0.0.1:0");
  if ((target.port || "80") === "8787" || !["127.0.0.1", "localhost"].includes(target.hostname)) {
    console.error(`${NAME}: refusing ${target.host}. This check writes. Run it against a `
      + "throwaway server -- scripts/claude/browser_checks.sh starts one.");
    process.exit(2);
  }
}

const base = process.argv[2];
const PORT = Number(process.env.CDP_PORT) || 9358;
const profile = mkdtempSync(join(tmpdir(), "newbox-check-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", "--use-fake-device-for-media-stream",
   `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let wsUrl;
for (let i = 0; i < 150 && !wsUrl; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((p) => p.type === "page")?.webSocketDebuggerUrl; } catch { /* not up */ }
  await sleep(100);
}
const ws = new WebSocket(wsUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pending = new Map();
const thrown = [];
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails.exception?.description || "?");
  pending.get(msg.id)?.(msg);
  pending.delete(msg.id);
};
const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description);
  return r.result?.result?.value;
};
const waitFor = async (expression, what, tries = 150) => {
  for (let i = 0; i < tries; i++) {
    try { if (await evaluate(expression)) return; } catch { /* between documents */ }
    await sleep(100);
  }
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
const press = async (key, code, vk, text = "") => {
  await send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", key, code, windowsVirtualKeyCode: vk, text });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: vk });
  await sleep(120);
};
const api = async (path) => (await fetch(`${base}/api${path}`)).json();
const camera = (allowed) => (allowed
  ? send("Browser.grantPermissions", { origin: base, permissions: ["videoCapture"] })
  : send("Browser.resetPermissions"));
// The whole field, as it stands: on the new-record page by default, and in
// the sheet's dialog under its own prefix -- one reading of the one field.
const field = (prefix = "new-photo") => evaluate(`(() => {
  const box = ${q(`#${prefix}-box`)};
  const cam = ${q(`#${prefix}-cam`)};
  const track = cam?.srcObject?.getVideoTracks?.()[0];
  return {
    box: Boolean(box) && !box.hidden,
    live: Boolean(cam) && !cam.hidden,
    frames: cam?.videoWidth || 0,
    track: track ? track.readyState : null,
    shutter: Boolean(${q(`#${prefix}-shutter`)}) && !${q(`#${prefix}-shutter`)}.hidden,
    retake: Boolean(${q(`#${prefix}-retake`)}) && !${q(`#${prefix}-retake`)}.hidden,
    still: Boolean(${q(`#${prefix}-still`)}) && !${q(`#${prefix}-still`)}.hidden,
    stillWidth: ${q(`#${prefix}-still`)}?.naturalWidth || 0,
    picker: Boolean(${q(`#${prefix}-shot`)}),
    said: ${q(`#${prefix}-line`)}?.textContent || "",
  };
})()`);
const goNew = async () => {
  await evaluate(`location.hash = "#/new"`);
  await waitFor(`Boolean(${q("#new #create")})`, "the new-record form");
  await sleep(200);
};
// Everything the page writes, so "no photo" can be shown to have sent none.
// The real fetch is kept once: wrapping a wrapper would count every request
// as many times as this has been called.
const watchWrites = () => evaluate(`(() => { window.__real = window.__real || window.fetch;
  window.__writes = [];
  window.fetch = (u, o = {}) => { window.__writes.push(((o.method) || "GET") + " " + u); return window.__real(u, o); }; })()`);
const writes = () => evaluate(`window.__writes.filter((w) => !w.startsWith("GET"))`);
const madeCode = async () => (await evaluate("location.hash")).split("/")[2];

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);

try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await camera(true);
  await send("Page.navigate", { url: `${base}/#/new` });
  await waitFor(`Boolean(${q("#new #create")})`, "the new-record form");

  // --- the viewfinder comes up by itself ---
  await waitFor(`${q("#new-photo-cam")}.videoWidth > 0`, "the camera to come up on the page", 200);
  let seen = await field();
  check("the camera comes up live on the new-record page, with a shutter",
        seen.live && seen.box && seen.track === "live" && seen.frames > 0 && seen.shutter && !seen.still,
        JSON.stringify(seen));
  check("and says what size it is, or what that costs", /Camera: \d+ × \d+|small labels/.test(seen.said), seen.said);
  check("the file picker is still there, as the other way to do it", seen.picker);

  // --- the buttons: making a record prints nothing ---
  const buttons = await evaluate(`[...document.querySelectorAll("#new button[type=submit]")].map((b) =>
    ({ id: b.id, print: b.dataset.print || "", quiet: b.classList.contains("quiet") }))`);
  check("the first button is the plain Create, and it prints nothing",
        buttons[0]?.id === "create" && !buttons[0].print && !buttons[0].quiet, JSON.stringify(buttons));
  check("the stub is the quiet one beside it, still one press away",
        buttons[1]?.id === "create-stub" && buttons[1].print === "stub" && buttons[1].quiet, JSON.stringify(buttons));

  // --- shutter, then create: the photograph lands on the record ---
  await click("#new-photo-shutter");
  await waitFor(`!${q("#new-photo-still")}.hidden`, "the frame just taken");
  seen = await field();
  check("the shutter keeps a still and offers another go",
        seen.still && !seen.live && seen.retake && !seen.shutter, JSON.stringify(seen));
  check("the frame kept is no bigger than the server would keep anyway",
        seen.stillWidth > 0 && seen.stillWidth <= 4096, String(seen.stillWidth));
  await click("#new [name=content_summary]");
  await type("kettle and mugs");
  await watchWrites();
  await click("#create");
  await waitFor(`location.hash.endsWith("/edit")`, "the record's editor");
  const withPhoto = await madeCode();
  const sent = await writes();
  check("Create makes the record and lands on its editor", Boolean(withPhoto), await evaluate("location.hash"));
  check("and the photograph is on it, being read",
        (await api(`/boxes/${withPhoto}/photos`)).length === 1,
        JSON.stringify((await api(`/boxes/${withPhoto}/photos`)).map((p) => p.bytes)));
  check("nothing was printed for it", sent.every((w) => !w.includes("/labels/print")), sent.join(" | "));

  // --- the camera is let go of when the page is left ---
  await goNew();
  await waitFor(`${q("#new-photo-cam")}.videoWidth > 0`, "the camera again", 200);
  await evaluate(`window.__track = ${q("#new-photo-cam")}.srcObject.getVideoTracks()[0]`);
  await evaluate(`location.hash = "#/"`);
  await waitFor(`Boolean(${q("#boxlist")}) || Boolean(document.querySelector(".empty"))`, "the list");
  await sleep(300);
  check("leaving the page stops the camera: the track is ended",
        (await evaluate(`window.__track.readyState`)) === "ended", await evaluate(`window.__track.readyState`));

  // --- with no photo, nothing changed ---
  await goNew();
  await watchWrites();
  await click("#create");
  await waitFor(`location.hash.endsWith("/edit")`, "the record made with no photo");
  const bare = await madeCode();
  const bareWrites = await writes();
  check("creating with no photo still makes a record, at one press",
        Boolean(bare) && bare !== withPhoto, bare);
  check("and sends nothing but the record itself",
        bareWrites.length === 1 && bareWrites[0] === "POST /api/boxes", bareWrites.join(" | "));
  check("which has no photo on it", (await api(`/boxes/${bare}/photos`)).length === 0);

  // --- the sheet's camera: the same field, in a dialog, on a record that exists ---
  //
  // That record, with no photo and the camera still granted. The sheet draws
  // no input of its own; the dialog is appended to <body>, outside #app. Each
  // photo is added the moment it is taken, and the viewfinder comes back.
  const noInput = () => evaluate(`document.querySelectorAll("#app input, #app textarea, #app select").length === 0`);
  const tiles = () => evaluate(`document.querySelectorAll("#view-photos .tile").length`);
  const openSheet = async () => {
    await evaluate(`location.hash = "#/b/${bare}"`);
    await waitFor(`Boolean(${q("#take-photo")})`, "the sheet, with its camera");
    await sleep(200);
  };
  const liveAgain = () => waitFor(`(() => { const c = ${q("#taker-cam")}; return Boolean(c) && !c.hidden
    && /Added/.test(${q("#taker-line")}?.textContent || ""); })()`, "the viewfinder back, and the photo added", 150);
  await openSheet();
  check("the sheet has a camera button beside Edit, named without a word",
        await evaluate(`(() => { const b = ${q("#take-photo")}; return Boolean(b) && b.type === "button"
          && /photo/i.test(b.getAttribute("aria-label") || "") && b.nextElementSibling === ${q("#edit")}; })()`));
  check("and still nothing on it to brush", await noInput());
  check("and no photo on it yet", (await tiles()) === 0, String(await tiles()));
  await evaluate(`window.__sheet = ${q("#sheet")}; window.__facts = ${q("#facts")};`);
  await watchWrites();
  await click("#take-photo");
  await waitFor(`Boolean(document.querySelector("dialog.taker[open]"))`, "the camera dialog");
  await waitFor(`${q("#taker-cam")}?.videoWidth > 0`, "the camera to come up in the dialog", 200);
  seen = await field("taker");
  check("pressing it opens a dialog with the camera live and a shutter",
        seen.live && seen.box && seen.track === "live" && seen.frames > 0 && seen.shutter && !seen.still,
        JSON.stringify(seen));
  check("the dialog is outside the sheet, which still draws no input",
        (await evaluate(`!document.querySelector("#app dialog.taker")`)) && await noInput());
  check("and there is no Add button: taking is adding",
        await evaluate(`!document.querySelector("#taker-add") && Boolean(document.querySelector("#taker-done"))`));
  await click("#taker-shutter");
  await liveAgain();
  await waitFor(`document.querySelectorAll("#view-photos .tile").length === 1`, "the new tile on the sheet", 100);
  let added = await writes();
  check("the shutter alone sends the photo, and nothing else",
        added.length === 1 && added[0] === `POST /api/boxes/${bare}/photos`, added.join(" | "));
  check("and it is on the record", (await api(`/boxes/${bare}/photos`)).length === 1);
  seen = await field("taker");
  check("the viewfinder is straight back for the next one, and the line says it was added",
        seen.live && seen.shutter && !seen.still && seen.track === "live" && /Added/.test(seen.said), JSON.stringify(seen));
  check("the sheet behind shows it in place, without being redrawn",
        await evaluate(`${q("#sheet")} === window.__sheet && ${q("#facts")} === window.__facts
          && !${q("#sheet")}.hidden && ${q("#photo-count b")}?.textContent === "1"`),
        await evaluate(`${q("#photo-count b")}?.textContent`));
  // A second, straight after: the whole point is a quick run of them. The fake
  // webcam's frames differ over time, so wait for a new one before the shutter.
  await sleep(600);
  await click("#taker-shutter");
  await waitFor(`/2 so far/.test(${q("#taker-line")}?.textContent || "")`, "the second photo added", 150);
  added = await writes();
  check("a second press adds a second photo, and counts them",
        added.length === 2 && (await api(`/boxes/${bare}/photos`)).length === 2,
        `${added.join(" | ")}; ${(await api(`/boxes/${bare}/photos`)).length} on the record`);
  await evaluate(`window.__track = ${q("#taker-cam")}.srcObject.getVideoTracks()[0]`);
  await click("#taker-done");
  await waitFor(`!document.querySelector("dialog.taker")`, "Done to close the dialog");
  await waitFor(`document.querySelectorAll("#view-photos .tile").length === 2`, "both tiles on the sheet", 100);
  check("Done closes it and stops the camera",
        (await evaluate(`window.__track.readyState`)) === "ended", await evaluate(`window.__track.readyState`));
  check("and puts the focus back on the button",
        (await evaluate(`document.activeElement?.id`)) === "take-photo", await evaluate(`document.activeElement?.id`));
  check("the sheet still draws no input", await noInput());

  // --- opening it and leaving sends nothing: Done, or Escape ---
  await watchWrites();
  await click("#take-photo");
  await waitFor(`${q("#taker-cam")}?.videoWidth > 0`, "the camera again, in the dialog", 200);
  await evaluate(`window.__track = ${q("#taker-cam")}.srcObject.getVideoTracks()[0]`);
  await click("#taker-done");
  await waitFor(`!document.querySelector("dialog.taker")`, "Done, with nothing taken");
  check("Done with nothing taken sends nothing and stops the camera",
        (await writes()).length === 0 && (await evaluate(`window.__track.readyState`)) === "ended",
        (await writes()).join(" | "));
  await click("#take-photo");
  await waitFor(`${q("#taker-cam")}?.videoWidth > 0`, "the camera once more", 200);
  await press("Escape", "Escape", 27);
  await waitFor(`!document.querySelector("dialog.taker")`, "Escape to close the dialog");
  check("Escape closes it, and nothing was sent", (await writes()).length === 0, (await writes()).join(" | "));
  check("and the record has the two photos it was given", (await api(`/boxes/${bare}/photos`)).length === 2);

  // --- an upload that fails keeps the photo, says why, and goes again ---
  // The next photo POST is refused in the page, as a dropped connection would be.
  await click("#take-photo");
  await waitFor(`${q("#taker-cam")}?.videoWidth > 0`, "the camera, to fail an upload", 200);
  await evaluate(`(() => { const real = window.__real || window.fetch; let once = true;
    window.fetch = (u, o = {}) => (once && (o.method || "GET") === "POST" && String(u).endsWith("/photos")
      ? (once = false, Promise.reject(new TypeError("Network down"))) : real(u, o)); })()`);
  await sleep(600);
  await click("#taker-shutter");
  await waitFor(`/did not upload/.test(${q("#taker-line")}?.textContent || "")`, "the failure to be said", 100);
  seen = await field("taker");
  check("a failed upload keeps the photo on screen, says why, and offers Try again",
        seen.still && /Network down/.test(seen.said)
          && await evaluate(`!${q("#taker-again")}.hidden`), JSON.stringify(seen));
  await click("#taker-again");
  await liveAgain();
  check("Try again sends it, and the viewfinder comes back",
        (await api(`/boxes/${bare}/photos`)).length === 3 && await evaluate(`${q("#taker-again")}.hidden`),
        String((await api(`/boxes/${bare}/photos`)).length));
  await click("#taker-done");
  await waitFor(`!document.querySelector("dialog.taker")`, "Done, after a retry");

  // --- Enter still reaches the plain Create, and spends no tape ---
  await goNew();
  await watchWrites();
  await click("#new [name=source_location]");
  await type("shelf 3");
  await press("Enter", "Enter", 13, "\r");
  await waitFor(`location.hash.endsWith("/edit")`, "the record Enter made");
  const byEnter = await writes();
  check("Enter creates the record", (await madeCode()) !== bare, await evaluate("location.hash"));
  check("and never spends tape", byEnter.every((w) => !w.includes("/labels/print")), byEnter.join(" | "));

  // --- not allowed yet: nothing is asked for until it is asked for ---
  //
  // Opening New is not asking for a camera, and a phone that said no once
  // should not be asked again on every visit -- so the page only starts one
  // where the permission is already granted, and "Use the camera" is the way
  // in otherwise.
  await camera(false);
  await goNew();
  await sleep(800);
  seen = await field();
  check("with the camera not allowed yet, none is asked for",
        !seen.box && !seen.live && seen.track === null, JSON.stringify(seen));
  check("and the way to one is a button, beside the picker",
        (await evaluate(`Boolean(${q("#new-photo-camera")}) && !${q("#new-photo-camera")}.hidden`)) && seen.picker);

  // --- and pressing it, with the camera refused, is ordinary ---
  await click("#new-photo-camera");
  await waitFor(`/declined|no camera|would not start/i.test(${q("#new-photo-line")}.textContent)`,
                "the refusal to be said", 200);
  seen = await field();
  check("a refused camera says so, with no dead grey box",
        !seen.box && !seen.live && seen.track === null, JSON.stringify(seen));
  check("nothing about it reads as an error", !/error|failed/i.test(seen.said), seen.said);
  check("and it points at the way that still works, which is still there",
        /[Cc]hoose a photo/.test(seen.said) && seen.picker, seen.said);
  await watchWrites();
  await click("#create");
  await waitFor(`location.hash.endsWith("/edit")`, "a record made with no camera at all");
  check("creating still works with no camera", (await api(`/boxes/${await madeCode()}/photos`)).length === 0);

  // --- and on the sheet, a refused camera is just as ordinary ---
  await openSheet();
  await watchWrites();
  await click("#take-photo");
  await waitFor(`/declined|no camera|would not start/i.test(${q("#taker-line")}?.textContent || "")`,
                "the refusal to be said in the dialog", 200);
  seen = await field("taker");
  check("the sheet's dialog says the camera was refused, and the picker is still there",
        !seen.box && !seen.live && seen.track === null && seen.picker && /[Cc]hoose a photo/.test(seen.said),
        JSON.stringify(seen));
  await click("#taker-done");
  await waitFor(`!document.querySelector("dialog.taker")`, "Done, after a refusal");
  check("and Done leaves nothing behind", (await writes()).length === 0, (await writes()).join(" | "));
  await camera(true);

  check("nothing threw in the page", thrown.length === 0, thrown.join(" | "));
} catch (error) {
  check(`harness: ${error.message}`, false);
}

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close();
chrome.kill();
await sleep(300);
try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* the OS will */ }
process.exit(failures ? 1 : 0);
