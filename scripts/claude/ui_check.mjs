#!/usr/bin/env node
// Purpose: click through the box page in a real (headless) browser and check
//          that its forms behave -- the thing `node --check` and the static
//          guards cannot see. Written after three patches to web/app.js
//          half-landed and shipped dead buttons.
// Date:    2026-09-18
// Usage:   node scripts/claude/ui_check.mjs [base-url] [box-code]
//          defaults: http://127.0.0.1:8788  B-0004   (i.e. `make run`)
//
// Saves nothing: it only edits fields and cancels, and it counts the writes
// the page makes while it does so to prove that. Needs Node 22+ (global
// WebSocket) and Google Chrome. No npm packages.
//
// The write paths (a rename that saves, a photo being read) are deliberately
// not here: they need a throwaway server, and this must stay safe to point at
// real data.

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
  for (let i = 0; i < 150; i++) {
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
  if (other) {   // a database with no rooms seeded has nothing to change to
    room.value = other.value;
    room.dispatchEvent(new Event("change", { bubbles: true }));
    check("changing the destination reveals Cancel", !roomCancel.hidden);
    roomCancel.click();
    check("Cancel restores the destination", room.value === before, room.value);
  }

  // Location form.
  const where = $("#location [name=current_location]");
  const whereCancel = $("#location [data-cancel]");
  const whereBefore = where.value;
  where.value = "somewhere else";
  where.dispatchEvent(new Event("input", { bubbles: true }));
  check("editing the location reveals Cancel", !whereCancel.hidden);
  whereCancel.click();
  check("Cancel restores the location", where.value === whereBefore, where.value);

  // Photos are read automatically now; the manual draft button and its review
  // panel must be gone, not merely unreachable.
  check("the Draft contents button is gone", !$("#draft-btn") && !$("#draft-panel"));

  // Every write the page attempts from here on is counted. Renaming and then
  // backing out must not reach the server at all.
  const writes = [];
  const realFetch = window.fetch;
  window.fetch = (url, options = {}) => {
    if ((options.method || "GET").toUpperCase() !== "GET") writes.push(`${options.method} ${url}`);
    return realFetch(url, options);
  };

  // Item names: tap to rename. Needs a box with at least one item.
  const names = Array.from(document.querySelectorAll("#items .name"));
  if (names.length) {
    const drawn = await fetch(`/api/boxes/${location.hash.split("/").pop()}/items`).then((r) => r.json());
    check("the list draws every item the server has", names.length === drawn.length,
          `${names.length} drawn, ${drawn.length} on the server`);
    check("every item name is a focusable control",
          names.every((n) => n.tagName === "BUTTON" && n.tabIndex >= 0));
    check("every item name says what pressing it does",
          names.every((n) => n.getAttribute("aria-label") === `Rename ${n.textContent}`),
          names.map((n) => n.getAttribute("aria-label")).join(", "));

    const name = names[0];
    const text = name.textContent;
    name.focus();
    check("an item name takes the focus", document.activeElement === name);
    name.click();
    let field = $("#items input.rename");
    check("pressing a name opens a field in its place", Boolean(field) && name.hidden);
    check("the field holds the name, focused and selected",
          field?.value === text && document.activeElement === field
            && field.selectionStart === 0 && field.selectionEnd === text.length,
          field?.value);
    field.value = `${text} EDITED`;
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    check("Escape closes the field and puts the name back",
          !$("#items input.rename") && !name.hidden && name.textContent === text, name.textContent);
    check("Escape returns the focus to the name", document.activeElement === name);

    // Unchanged, then Enter: a cancel by another name.
    name.click();
    field = $("#items input.rename");
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    check("Enter on an unchanged name closes the field", !$("#items input.rename") && !name.hidden);

    // Emptied, then blur.
    name.click();
    field = $("#items input.rename");
    field.value = "   ";
    field.blur();
    check("leaving an emptied field keeps the old name",
          !$("#items input.rename") && name.textContent === text, name.textContent);

    check("no field is left behind to look like an unsaved edit", !$("#items input"));
    check("none of that wrote anything", writes.length === 0, writes.join("; "));
  }

  // Photos: each figure says what the vision model made of it.
  const figures = Array.from(document.querySelectorAll("#shots figure"));
  if (figures.length) {
    const onServer = await fetch(`/api/boxes/${location.hash.split("/").pop()}/photos`).then((r) => r.json());
    check("the strip draws every photo the server has, once each",
          figures.length === onServer.length
            && new Set(figures.map((f) => f.dataset.key)).size === figures.length
            && onServer.every((p) => figures.some((f) => f.dataset.key === String(p.id))),
          `${figures.length} drawn, ${onServer.length} on the server`);
    const known = ["none", "pending", "running", "done", "error"];
    check("every photo shows an analysis state",
          figures.every((f) => known.includes(f.querySelector(".analysis")?.dataset.analysis)),
          figures.map((f) => f.querySelector(".analysis")?.dataset.analysis).join(", "));
    check("a ring shows on exactly the photos still being read",
          figures.every((f) => {
            const state = f.querySelector(".analysis").dataset.analysis;
            return f.querySelector(".ring").hidden === !["pending", "running"].includes(state);
          }));
    check("a finished or failed photo says something",
          figures.every((f) => {
            const block = f.querySelector(".analysis");
            return !["done", "error"].includes(block.dataset.analysis)
              || block.querySelector(".state").textContent.trim() !== "";
          }));
    check("Retry is offered on exactly the photos that failed",
          figures.every((f) => {
            const block = f.querySelector(".analysis");
            const button = block.querySelector("[data-analyse]");
            return block.dataset.analysis !== "error"
              || (!button.hidden && button.textContent === "Retry");
          }));
    check("exactly one photo is marked as the cover",
          figures.filter((f) => !f.querySelector(".mark").hidden).length === 1);

    // Delete asks first, and still does after the strip has been redrawn in
    // place -- rebinding is the usual way these go dead. Cancel path only.
    const drop = figures[0].querySelector("[data-drop-photo]");
    drop.click();
    await wait(() => $("dialog.ask")?.open, "the photo delete confirmation");
    check("deleting a photo asks first", $("dialog.ask h2").textContent === "Delete this photo?");
    $("dialog.ask [value=no]").click();
    await wait(() => !$("dialog.ask"), "the confirmation to close");
    check("cancelling deleted no photo",
          document.querySelectorAll("#shots figure").length === figures.length && writes.length === 0,
          writes.join("; "));
  }
  window.fetch = realFetch;

  // Delete asks first. Only the Cancel path runs here -- this may be real data.
  const del = $("#delete");
  if (del) {
    const here = location.hash;
    del.click();
    await wait(() => $("dialog.ask")?.open, "the delete confirmation");
    check("Delete opens a confirmation instead of deleting", $("dialog.ask")?.open);
    check("Cancel holds the focus, not the destructive button",
          document.activeElement?.value === "no", document.activeElement?.textContent);
    check("the confirmation names the record",
          $("dialog.ask h2").textContent.includes(here.split("/").pop()), $("dialog.ask h2").textContent);
    $("dialog.ask [value=no]").click();
    await wait(() => !$("dialog.ask"), "the confirmation to close");
    check("cancelling leaves you on the record", location.hash === here && Boolean($("#summary-form")));
    const still = await fetch(`/api/boxes/${here.split("/").pop()}`).then((r) => r.json());
    check("cancelling deleted nothing", !(still.box || still).deleted_at);
  }

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

  // The way home: top left, on every page, and it goes to the list.
  const home = document.querySelector("a.home");
  const box = home?.getBoundingClientRect();
  check("the home mark is in the top-left corner",
        Boolean(box) && box.top < 80 && box.left < window.innerWidth / 3, JSON.stringify(box));
  check("it is a link to the list, with a name a screen reader can say",
        home?.getAttribute("href") === "#/" && home?.getAttribute("aria-label") === "Home");
  check("it is big enough to hit", Boolean(box) && box.height >= 44 && box.width >= 44,
        `${box?.width}x${box?.height}`);
  const buttons = Array.from(document.querySelectorAll("#new button[type=submit]"));
  const kind = document.querySelector("#new [name=kind]");

  const create = document.querySelector("#new #create");
  check("the first button creates and prints the stub",
        buttons[0]?.dataset.print === "stub", buttons[0]?.textContent);
  check("the plain Create prints nothing",
        buttons.includes(create) && !create.hasAttribute("data-print"), create?.textContent);
  check("the last button is the one that prints the full label",
        buttons.at(-1)?.dataset.print === "label", buttons.at(-1)?.textContent);
  check("Create names the kind it makes",
        /^Create \w+$/.test(create.textContent), create.textContent);

  const other = Array.from(kind.options).find((o) => o.value !== kind.value);
  if (other) {
    kind.value = other.value;
    kind.dispatchEvent(new Event("change", { bubbles: true }));
    check("changing the kind renames Create",
          create.textContent === `Create ${other.textContent.trim().toLowerCase()}`,
          create.textContent);
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

  // An exception inside a click handler or a timer fails no check above on
  // its own -- the page just quietly stops doing something. Collect them.
  const thrown = [];
  const listeners = { "Runtime.exceptionThrown": (p) =>
    thrown.push(p.exceptionDetails.exception?.description || p.exceptionDetails.text) };
  const routed = ws.onmessage;
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.method && listeners[msg.method]) listeners[msg.method](msg.params);
    routed(m);
  };
  await send("Runtime.enable");
  // A headless page does not have the focus (document.hasFocus() is false),
  // and an unfocused page moves activeElement about without firing a single
  // focus or blur event. Blur is what saves a rename, so without this the
  // check below fails against code that works in every real browser.
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });

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
  results.push(["nothing threw in the page while all that happened",
                thrown.length === 0, thrown.join(" | ")]);
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
