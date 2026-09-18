#!/usr/bin/env node
// Purpose: click through the box page in a real (headless) browser and check
//          that its forms behave -- the thing `node --check` and the static
//          guards cannot see. Written after three patches to web/app.js
//          half-landed and shipped dead buttons.
// Date:    2026-09-18
// Usage:   node scripts/claude/ui_check.mjs [base-url] [box-code]
//          defaults: http://127.0.0.1:8788  B-0004   (i.e. `make run`)
//
// Saves nothing: it only edits fields and cancels. Needs Node 22+ (global
// WebSocket) and Google Chrome. No npm packages.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2] || "http://127.0.0.1:8788";
const code = process.argv[3] || "B-0004";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;

const profile = mkdtempSync(join(tmpdir(), "ui-check-"));
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = pages.find((p) => p.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(100);
  }
  throw new Error("Chrome did not start");
}

// Runs inside the page. Returns a list of [name, passed, detail].
const IN_PAGE = async () => {
  const wait = async (test, what) => {
    for (let i = 0; i < 100; i++) {
      if (test()) return;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error(`timed out waiting for ${what}`);
  };
  const $ = (s) => document.querySelector(s);
  const results = [];
  const check = (name, passed, detail = "") => results.push([name, Boolean(passed), String(detail)]);

  await wait(() => $("#summary-form"), "the box page");
  const field = $("#summary-form [name=content_summary]");
  const cancel = $("#summary-form [data-cancel]");
  const saved = field.value;

  check("summary Cancel is hidden on an untouched page", cancel.hidden);

  // Typing shows Cancel; Cancel restores.
  field.value = saved + " EDITED";
  field.dispatchEvent(new Event("input", { bubbles: true }));
  check("typing reveals Cancel", !cancel.hidden);
  cancel.click();
  check("Cancel restores the typed-over summary", field.value === saved, field.value);
  check("Cancel hides itself afterwards", cancel.hidden);

  // From contents, then Cancel.
  const suggest = $("#suggest");
  if (suggest) {
    suggest.click();
    await wait(() => !suggest.disabled && ($("#oops")?.open || field.value !== saved || !$("#say").hidden),
               "From contents to answer");
    check("From contents raises no error dialog", !$("#oops")?.open, $("#oops p")?.textContent || "");
    if (field.value !== saved) {
      check("a suggestion reveals Cancel", !cancel.hidden);
      cancel.click();
      check("Cancel takes the suggestion back", field.value === saved, field.value);
    }
  }

  // Destination form: a select change is cancellable too.
  const room = $("#destination [name=destination_room_id]");
  const roomCancel = $("#destination [data-cancel]");
  const before = room.value;
  const other = Array.from(room.options).find((o) => o.value !== before);
  room.value = other.value;
  room.dispatchEvent(new Event("change", { bubbles: true }));
  check("changing the destination reveals Cancel", !roomCancel.hidden);
  roomCancel.click();
  check("Cancel restores the destination", room.value === before, room.value);

  // Location form.
  const where = $("#location [name=current_location]");
  const whereCancel = $("#location [data-cancel]");
  const whereBefore = where.value;
  where.value = "somewhere else";
  where.dispatchEvent(new Event("input", { bubbles: true }));
  check("editing the location reveals Cancel", !whereCancel.hidden);
  whereCancel.click();
  check("Cancel restores the location", where.value === whereBefore, where.value);

  return results;
};

// The new-record form. Looks, never submits: creating would write a real row.
const IN_NEW = async () => {
  const wait = async (test, what) => {
    for (let i = 0; i < 100; i++) {
      if (test()) return;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error(`timed out waiting for ${what}`);
  };
  const results = [];
  const check = (name, passed, detail = "") => results.push([name, Boolean(passed), String(detail)]);

  await wait(() => document.querySelector("#new #create"), "the new form");
  const buttons = Array.from(document.querySelectorAll("#new button[type=submit]"));
  const kind = document.querySelector("#new [name=kind]");

  check("the first button creates without printing",
        buttons[0]?.id === "create" && !buttons[0].hasAttribute("data-print"), buttons[0]?.textContent);
  check("the second button is the one that prints",
        buttons[1]?.hasAttribute("data-print"), buttons[1]?.textContent);
  check("the first button names the kind it makes",
        /^Create \w+/.test(buttons[0].textContent) && !/print/i.test(buttons[0].textContent),
        buttons[0].textContent);

  const other = Array.from(kind.options).find((o) => o.value !== kind.value);
  if (other) {
    kind.value = other.value;
    kind.dispatchEvent(new Event("change", { bubbles: true }));
    check("changing the kind renames the button",
          buttons[0].textContent === `Create ${other.textContent.trim().toLowerCase()}`,
          buttons[0].textContent);
  }
  return results;
};

let failures = 1;
try {
  const ws = new WebSocket(await target());
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    pending.set(++id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });

  await send("Page.enable");
  await send("Page.navigate", { url: `${base}/#/b/${encodeURIComponent(code)}` });
  await sleep(500);
  const reply = await send("Runtime.evaluate", {
    expression: `(${IN_PAGE.toString()})()`, awaitPromise: true, returnByValue: true,
  });
  if (reply.result?.exceptionDetails) {
    throw new Error(reply.result.exceptionDetails.exception?.description || "page script failed");
  }
  const results = reply.result.result.value;

  await send("Page.navigate", { url: `${base}/#/new` });
  await sleep(300);
  const second = await send("Runtime.evaluate", {
    expression: `(${IN_NEW.toString()})()`, awaitPromise: true, returnByValue: true,
  });
  if (second.result?.exceptionDetails) {
    throw new Error(second.result.exceptionDetails.exception?.description || "new-form script failed");
  }
  results.push(...second.result.result.value);
  failures = 0;
  for (const [name, passed, detail] of results) {
    if (!passed) failures++;
    console.log(`${passed ? "ok  " : "FAIL"}  ${name}${!passed && detail ? `  -> ${detail}` : ""}`);
  }
  console.log(failures ? `\n${failures} failed` : `\nall ${results.length} passed`);
  ws.close();
} catch (error) {
  console.error(`ui_check: ${error.message}`);
} finally {
  chrome.kill();
  await sleep(200);
  rmSync(profile, { recursive: true, force: true });
}
process.exit(failures ? 1 : 0);
