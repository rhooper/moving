#!/usr/bin/env node
// Purpose: the photo viewer, clicked for real at phone width: a tap opens it
//          rather than a new tab; opened mid-read it says so, then fills in BY
//          ITSELF; Escape, Close and a tap outside close it, a tap inside does
//          not; the thumbnail is still a real link. And "read again": it runs
//          the QUICK read, takes nothing away from the record, and is not
//          offered for a single thing's photo.
// Date:    2026-09-18
// Usage:   node scripts/claude/viewer_check.mjs <base-url> <CODE> <photo.jpg> <out-dir>
//          WRITES: uploads a photo. Refuses the live service. Needs the stub
//          vision provider with a few seconds' delay, so there is a "still being
//          read" state to catch. scripts/claude/browser_checks.sh sets that up.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const NAME = "viewer_check";
// This writes, so never port 8787 -- the live service, with the real database
// and real tape -- nor any host that is not this machine.
{
  const target = new URL(process.argv[2] || "http://127.0.0.1:0");
  if ((target.port || "80") === "8787" || !["127.0.0.1", "localhost"].includes(target.hostname)) {
    console.error(`${NAME}: refusing ${target.host}. This check writes. Run it against a ` +
      "throwaway server -- scripts/claude/browser_checks.sh starts one.");
    process.exit(2);
  }
}

const [base, code, photoPath, outDir] = process.argv.slice(2);
// Overridable: this is not the only headless Chrome on the machine.
const PORT = Number(process.env.CDP_PORT) || 9352;
const profile = mkdtempSync(join(tmpdir(), "viewer-check-"));
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
   "--window-size=420,900", "about:blank"], { stdio: "ignore" });
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
const waitFor = async (expression, what, tries = 150) => {
  for (let i = 0; i < tries; i++) { if (await evaluate(expression)) return; await sleep(100); }
  throw new Error(`timed out: ${what}`);
};
const shot = async (name) => {
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(outDir, name), Buffer.from(r.result.data, "base64"));
};

const results = [];
const check = (name, ok, detail = "") => results.push([name, Boolean(ok), String(detail)]);
const thrown = [];

try {
  await send("Page.enable");
  await send("Runtime.enable");
  ws.addEventListener("message", (m) => {
    const msg = JSON.parse(m.data);
    if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails.exception?.description || "?");
  });
  await send("Emulation.setDeviceMetricsOverride", { width: 400, height: 860, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: `${base}/#/b/${code}/edit` });
  await waitFor(`Boolean(document.getElementById("shot"))`, "the record page");

  // Upload through the API (the page hears about it over the websocket).
  const form = new FormData();
  form.append("file", new Blob([readFileSync(photoPath)], { type: "image/jpeg" }), "p.jpg");
  await fetch(`${base}/api/boxes/${code}/photos`, { method: "POST", body: form });
  await waitFor(`Boolean(document.querySelector(".shots figure .pic a"))`, "the photo to appear in place");

  // --- open it while it is still being read ---
  await evaluate(`document.querySelector(".shots figure .pic a").click()`);
  await waitFor(`Boolean(document.querySelector("dialog.viewer")?.open)`, "the viewer");
  check("a tap opens the viewer, not a new tab", await evaluate(`location.hash`) === `#/b/${code}/edit`);
  check("it says the photo is still being read",
        /being read/.test(await evaluate(`document.querySelector("dialog.viewer .note").textContent`)),
        await evaluate(`document.querySelector("dialog.viewer .note").textContent`));
  check("and lists nothing yet", await evaluate(`document.querySelector("dialog.viewer ul.items").hidden`));
  check("the full picture is what it shows",
        /\/photos\/\d+\/full$/.test(await evaluate(`document.querySelector("dialog.viewer img").getAttribute("src")`)));
  await shot("viewer-busy.png");

  // --- and it fills in by itself when the model finishes ---
  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "done"`, "the viewer to fill in", 200);
  const names = await evaluate(`[...document.querySelectorAll("dialog.viewer ul.items li")].map((li) => li.innerText.replace(/\\s+/g, " ").trim())`);
  check("the open viewer fills in when the job ends, without being reopened",
        JSON.stringify(names) === JSON.stringify(["kettle", "mug ×3", "toaster"]), JSON.stringify(names));
  check("with the model's own sentence about the photo",
        (await evaluate(`document.querySelector("dialog.viewer .summary").textContent`)) === "kettle, mugs and a toaster");
  check("the note about reading has gone", await evaluate(`document.querySelector("dialog.viewer .note").hidden`));
  await shot("viewer-done.png");
  check("nothing sticks out sideways at phone width",
        await evaluate(`document.querySelector("dialog.viewer").scrollWidth <= document.querySelector("dialog.viewer").clientWidth + 1`));

  // --- a closer look, on request ---
  const button = (which) => evaluate(`(() => { const b = document.querySelector("dialog.viewer [data-${which}]");
    return b && !b.hidden && !b.closest("[hidden]") ? b.textContent.trim() : null; })()`);
  check("after a quick read the read button says Read again", (await button("rerun")) === "Read again", await button("rerun"));
  check("a quick read offers a closer look", (await button("closer")) === "Look closer", await button("closer"));
  await evaluate(`document.querySelector("dialog.viewer [data-closer]").click()`);
  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "busy"`, "the closer look to start");
  check("asking says a closer look is under way, in the viewer",
        /closer look/.test(await evaluate(`document.querySelector("dialog.viewer .note").textContent`)),
        await evaluate(`document.querySelector("dialog.viewer .note").textContent`));
  check("both buttons go away while it runs", (await button("closer")) === null && (await button("rerun")) === null);
  await waitFor(`/Looking closer|Queued/.test(document.querySelector(".shots figure .analysis .state")?.textContent || "")`,
                "the strip to say it is looking closer");
  check("and under the photo in the strip", true);
  await shot("viewer-closer-busy.png");

  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "done"`, "the closer look to finish", 250);
  const closerNames = await evaluate(`[...document.querySelectorAll("dialog.viewer ul.items li")].map((li) => li.innerText.replace(/\\s+/g, " ").trim())`);
  check("the viewer fills in with what the closer look saw",
        JSON.stringify(closerNames) === JSON.stringify(["kettle", "mug ×4", "toaster", "Dualit toaster manual"]),
        JSON.stringify(closerNames));
  check("and says that is what this was",
        (await evaluate(`document.querySelector("dialog.viewer h2").textContent`)) === "Seen on a closer look");
  check("a closer look is not offered twice", (await button("closer")) === null);
  check("but the ordinary read is still on offer after it", (await button("rerun")) === "Read again", await button("rerun"));
  const merged = await (await fetch(`${base}/api/boxes/${code}/items`)).json();
  check("the record gained what was new, and the better count, with no duplicates",
        JSON.stringify(merged.map((i) => [i.name, i.qty]).sort()) ===
          JSON.stringify([["Dualit toaster manual", 1], ["kettle", 1], ["mug", 4], ["toaster", 1]]),
        JSON.stringify(merged.map((i) => [i.name, i.qty])));
  await shot("viewer-closer-done.png");

  // --- read again: the ORDINARY read, and it takes nothing away ---
  const before = JSON.stringify(merged.map((i) => [i.name, i.qty]).sort());
  await evaluate(`document.querySelector("dialog.viewer [data-rerun]").click()`);
  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "busy"`, "the re-read to start");
  check("pressing Read again hides both buttons while it runs",
        (await button("rerun")) === null && (await button("closer")) === null);
  const running = await evaluate(`document.querySelector("dialog.viewer .note").textContent`);
  check("and it is the ordinary read that runs, not another closer look",
        /still being read/.test(running) && !/closer look/i.test(running), running);
  await shot("viewer-rerun-busy.png");
  await waitFor(`document.querySelector("dialog.viewer")?.dataset.state === "done"`, "the re-read to finish", 250);
  check("when it is done the heading is the ordinary one again",
        (await evaluate(`document.querySelector("dialog.viewer h2").textContent`)) === "Seen in this photo",
        await evaluate(`document.querySelector("dialog.viewer h2").textContent`));
  const rereadNames = await evaluate(`[...document.querySelectorAll("dialog.viewer ul.items li")].map((li) => li.innerText.replace(/\\s+/g, " ").trim())`);
  check("the viewer shows what THIS read saw",
        JSON.stringify(rereadNames) === JSON.stringify(["kettle", "mug ×3", "toaster"]), JSON.stringify(rereadNames));
  check("and a closer look is on offer again", (await button("closer")) === "Look closer" && (await button("rerun")) === "Read again");
  const after = await (await fetch(`${base}/api/boxes/${code}/items`)).json();
  check("re-reading removed nothing from the record and lowered no count",
        JSON.stringify(after.map((i) => [i.name, i.qty]).sort()) === before,
        JSON.stringify(after.map((i) => [i.name, i.qty]).sort()));
  await shot("viewer-rerun-done.png");

  // --- closing ---
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await waitFor(`!document.querySelector("dialog.viewer")`, "Escape to close it");
  check("Escape closes it and takes it off the page", true);

  await evaluate(`document.querySelector(".shots figure .pic a").click()`);
  await waitFor(`Boolean(document.querySelector("dialog.viewer")?.open)`, "the viewer again");
  // Whatever the last read of this photo was: here, the quick one just re-run.
  check("reopened, it shows the last read's findings straight away",
        (await evaluate(`document.querySelectorAll("dialog.viewer ul.items li").length`)) === 3);
  // A click on the backdrop lands on the dialog element itself.
  await evaluate(`document.querySelector("dialog.viewer").dispatchEvent(new MouseEvent("click", { bubbles: true }))`);
  await waitFor(`!document.querySelector("dialog.viewer")`, "the backdrop to close it");
  check("a tap outside closes it", true);

  await evaluate(`document.querySelector(".shots figure .pic a").click()`);
  await waitFor(`Boolean(document.querySelector("dialog.viewer")?.open)`, "the viewer a third time");
  await evaluate(`document.querySelector("dialog.viewer .seen li span").click()`);
  await sleep(200);
  check("a tap inside does not close it", await evaluate(`Boolean(document.querySelector("dialog.viewer")?.open)`));
  await evaluate(`document.querySelector("dialog.viewer form button").click()`);
  await waitFor(`!document.querySelector("dialog.viewer")`, "Close to close it");
  check("the Close button closes it", true);

  // --- the rest of the strip still works ---
  check("the link is still a link, for a long press or middle click",
        /\/photos\/\d+\/full$/.test(await evaluate(`document.querySelector(".shots figure .pic a").getAttribute("href")`)));
  // --- a single thing: its photos are not read, and the viewer does not offer to ---
  const bike = (await (await fetch(`${base}/api/boxes`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind: "item", content_summary: "bicycle" }) })).json()).code;
  const bikeForm = new FormData();
  bikeForm.append("file", new Blob([readFileSync(photoPath)], { type: "image/jpeg" }), "bike.jpg");
  await fetch(`${base}/api/boxes/${bike}/photos`, { method: "POST", body: bikeForm });
  await evaluate(`location.hash = ${JSON.stringify(`#/b/${bike}/edit`)}`);
  await waitFor(`document.querySelector("h1.code")?.textContent === ${JSON.stringify(bike)} && Boolean(document.querySelector(".shots figure .pic a"))`,
                "the single thing's page and photo");
  await evaluate(`document.querySelector(".shots figure .pic a").click()`);
  await waitFor(`Boolean(document.querySelector("dialog.viewer")?.open)`, "the viewer on a single thing");
  check("a single thing's photo offers no read, of either kind",
        (await button("rerun")) === null && (await button("closer")) === null);
  const single = await evaluate(`document.querySelector("dialog.viewer .note").textContent`);
  check("and says why: it is a single thing", /single thing/.test(single), single);
  await shot("viewer-single-thing.png");
  await evaluate(`document.querySelector("dialog.viewer form button").click()`);
  await waitFor(`!document.querySelector("dialog.viewer")`, "that viewer to close");

  check("nothing threw in the page", thrown.length === 0, thrown.join(" | "));
} catch (error) { check(`harness: ${error.message}`, false); }

let failures = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}${!ok && detail ? `  -> ${detail}` : ""}`);
}
console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
ws.close(); chrome.kill(); await sleep(200); rmSync(profile, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
