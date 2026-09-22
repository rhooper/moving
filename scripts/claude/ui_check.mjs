#!/usr/bin/env node
// Purpose: click through the box page in a real (headless) browser and check
//          that its forms behave -- the thing `node --check` and the static
//          guards cannot see.
// Date:    2026-09-18
// Usage:   node scripts/claude/ui_check.mjs [base-url] [box-code]
//          defaults: http://127.0.0.1:8788  B-0004   (i.e. `make run`)
//
// Saves nothing, on a page that saves by itself: fields are changed and put
// straight back inside the pause autosave waits out, and every write the page
// attempts is counted to prove that none was made. Needs Node 22+ (global
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
// Overridable, because this is not the only headless Chrome on the machine:
// two checks on one debugging port drive each other's pages.
const PORT = Number(process.env.CDP_PORT) || 9333;

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

  // Every write the page attempts is counted from here. Everything below is
  // arranged to cause none: fields are changed and put back *at once*, and
  // never focused, so nothing is ever left.
  const writes = [];
  const realFetch = window.fetch;
  window.fetch = (url, options = {}) => {
    if ((options.method || "GET").toUpperCase() !== "GET") writes.push(`${options.method} ${url}`);
    return realFetch(url, options);
  };
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));

  const forms = ["summary-form", "destination", "location"].map((id) => document.getElementById(id));
  check("the record's three forms are there", forms.every(Boolean));
  check("none of them has a Save or a Cancel any more",
        forms.every((f) => !f.querySelector("button[type=submit], [data-cancel]")),
        forms.map((f) => f.querySelectorAll("button[type=submit], [data-cancel]").length).join(","));
  check("each has a status line that is a polite live region",
        forms.every((f) => f.querySelector(".autosave [role=status]")));
  check("an untouched page says nothing and offers no Undo",
        forms.every((f) => f.querySelector(".autosave-state").textContent === ""
                           && f.querySelector(".autosave .undo").hidden));
  for (const name of ["content_summary", "kind", "destination_room_id", "source_room_id",
                      "source_location", "current_location"]) {
    check(`the ${name} field is there`, Boolean($(`[name=${name}]`)));
  }
  check("the location saves only when left", $("[name=current_location]").dataset.autosave === "commit");
  check("the copies field is not one of the autosaved ones",
        $("#copies") && !$("#copies").closest("#summary-form, #destination, #location"));

  // The kind, its size and the two rooms are rows of pushbuttons: real radio
  // groups, looked at and -- for the one row where a press changes nothing --
  // pressed. The optional rows are not pressed here: a press there saves.
  const record = await fetch(`/api/boxes/${location.hash.split("/")[2]}`).then((r) => r.json());
  const kindsNow = await fetch("/api/settings/kinds").then((r) => r.json());
  const shapeNow = kindsNow.find((k) => k.kind === (record.box || record).kind);
  const rowFor = (name) => document.querySelector(`#destination .seg[data-name="${name}"]`);
  const expected = ["kind", ...(shapeNow.sizes.length ? ["size"] : []), "destination_room_id", "source_room_id"];
  check("the pushbutton rows are there, and no dropdown is left in the form",
        expected.every((n) => rowFor(n)) && !$("#destination select"),
        expected.filter((n) => !rowFor(n)).join(","));
  check("the size row is there exactly when this kind of thing has sizes",
        Boolean(rowFor("size")) === shapeNow.sizes.length > 0);
  const rowsNow = expected.map(rowFor).filter(Boolean);
  check("each row is a named group of real radio buttons",
        rowsNow.every((g) => g.tagName === "FIELDSET" && g.querySelector("legend")?.textContent.trim()
          && g.querySelectorAll("input[type=radio]").length > 1
          && [...g.querySelectorAll("input[type=radio]")].every((r) => r.name === g.dataset.name && r.labels.length === 1)));
  check("every button can be reached and focused from the keyboard",
        rowsNow.every((g) => [...g.querySelectorAll("input[type=radio]")].every((r) => {
          if (r.disabled || r.tabIndex < 0) return false;
          r.focus();
          return document.activeElement === r;
        })));
  document.activeElement?.blur();
  check("every button is at least 44 px tall and none cuts its words short",
        rowsNow.every((g) => [...g.querySelectorAll(".seg-row span")].every((f) =>
          f.getBoundingClientRect().height >= 44 && f.scrollWidth <= f.clientWidth + 1)));
  check("the kind must have a value; the size and the rooms may be cleared",
        !("optional" in rowFor("kind").dataset)
          && expected.filter((n) => n !== "kind").every((n) => "optional" in rowFor(n).dataset));
  check("an optional row says what nothing chosen means, or how to un-choose",
        expected.filter((n) => n !== "kind").every((n) => {
          const said = rowFor(n).querySelector(".seg-said").textContent;
          return rowFor(n).dataset.value === "" ? /^(No size|Not decided yet|Not recorded)$/.test(said) : said === "Tap it again to clear";
        }));
  check("the row shows what the server has",
        rowFor("kind").dataset.value === (record.box || record).kind
          && rowFor("destination_room_id").dataset.value === String((record.box || record).destination_room_id ?? ""));
  // Pressing the chosen kind: the one press on this page that must do nothing.
  const kindNow = rowFor("kind").querySelector("input:checked");
  kindNow.labels[0].click();
  kindNow.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
  check("the kind cannot be cleared: pressing the chosen one leaves it chosen",
        kindNow.checked && rowFor("kind").dataset.value === kindNow.value);
  check("the label count shown is this kind's own", $("#copies").value === String(shapeNow.copies), $("#copies").value);

  // Typed, then put back before the pause runs out: nothing to save.
  const field = $("#summary-form [name=content_summary]");
  const line = $("#summary-form .autosave-state");
  const saved = field.value;
  const lineBefore = $("#summary-form .autosave").getBoundingClientRect().height;
  field.value = saved + " EDITED";
  field.dispatchEvent(new Event("input", { bubbles: true }));
  check("typing says it is saving", line.textContent === "Saving…", line.textContent);
  check("the line appearing did not change its height",
        $("#summary-form .autosave").getBoundingClientRect().height === lineBefore);
  field.value = saved;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  check("putting the text back un-says it", line.textContent === "", line.textContent);

  // The same in a field that saves only when left.
  const where = $("#location [name=current_location]");
  const whereBefore = where.value;
  where.value = "somewhere else";
  where.dispatchEvent(new Event("input", { bubbles: true }));
  check("an edited location says how it gets saved",
        /when you leave the field/.test($("#location .autosave-state").textContent),
        $("#location .autosave-state").textContent);
  where.value = whereBefore;
  where.dispatchEvent(new Event("input", { bubbles: true }));

  // Well past the pause. Had either edit survived, it would have been sent.
  await pause(1600);
  check("typing and putting it straight back saved nothing", writes.length === 0, writes.join("; "));
  check("and left the fields as they were", field.value === saved && where.value === whereBefore);

  // "From contents" writes, so it is looked at and not pressed; nor are the
  // pickers, which save the moment they change. autosave_check.mjs presses
  // them, against a throwaway.
  check("From contents is offered on a record that holds contents",
        Boolean($("#suggest")) === Boolean($("#items")));

  // Photos are read automatically; the manual draft button and its review
  // panel must be gone, not merely unreachable.
  check("the Draft contents button is gone", !$("#draft-btn") && !$("#draft-panel"));

  // Item names: tap to rename. Needs a box with at least one item.
  const names = Array.from(document.querySelectorAll("#items .name"));
  if (names.length) {
    const drawn = await fetch(`/api/boxes/${location.hash.split("/")[2]}/items`).then((r) => r.json());
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
    const onServer = await fetch(`/api/boxes/${location.hash.split("/")[2]}/photos`).then((r) => r.json());
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
          $("dialog.ask h2").textContent.includes(here.split("/")[2]), $("dialog.ask h2").textContent);
    $("dialog.ask [value=no]").click();
    await wait(() => !$("dialog.ask"), "the confirmation to close");
    check("cancelling leaves you on the record", location.hash === here && Boolean($("#summary-form")));
    const still = await fetch(`/api/boxes/${here.split("/")[2]}`).then((r) => r.json());
    check("cancelling deleted nothing", !(still.box || still).deleted_at);
  }

  // Things inside things, on this record: the section is there for a container
  // and the way to put this record inside another. A look-up is a GET, so it
  // can be pressed; nothing here is put anywhere.
  const insideSection = $("#inside-section");
  check("a container has an 'Inside this …' section, named for its kind",
        Boolean(shapeNow.contents) === Boolean(insideSection)
          && (!insideSection || insideSection.querySelector("h2").textContent === `Inside this ${shapeNow.label.toLowerCase()}`),
        insideSection?.querySelector("h2")?.textContent);
  if (insideSection) {
    const kids = (record.box || record).children || [];
    check("it lists what is inside, one row each, like the list",
          insideSection.querySelectorAll("#inside li").length === kids.length
            && kids.every((k) => insideSection.querySelector(`#inside li[data-key="${k.code}"] a[href="#/b/${k.code}"]`)),
          `${insideSection.querySelectorAll("#inside li").length} rows for ${kids.length}`);
    check("with nothing inside it says so, and offers to add something -- a button, not a page",
          (kids.length > 0) !== !$("#inside-empty").hidden
            && $("#add-inside")?.tagName === "BUTTON" && !$("#add-inside").hasAttribute("href"));
    check("a container's rows do not each say they are in it", !insideSection.querySelector("#inside .at:not([hidden])"));
  }
  check("the way in: a code field, a Look up, and a line saying what it is inside",
        $("#container-code") && $("#container-look") && /^(Not inside anything\.|Inside )/.test($("#inside-of").textContent),
        $("#inside-of")?.textContent);
  check("nothing offers to put it anywhere until a code has been looked up", $("#container-acts").hidden && $("#container-found").hidden);
  // Looking up its own code: refused here, before any server is asked.
  $("#container-code").value = location.hash.split("/")[2];
  $("#container").requestSubmit();
  await wait(() => !$("#container-found").hidden, "the look-up of itself");
  check("it cannot be put inside itself, and says so", /itself/.test($("#container-found").textContent) && $("#container-acts").hidden,
        $("#container-found").textContent);
  $("#container-code").value = "";
  $("#container-code").dataset.initial = "";

  // A <use> pointing at a symbol that is not there draws nothing, silently --
  // no console error, no broken-image box -- so the marks are resolved and
  // measured here rather than trusted.
  const marks = Array.from(document.querySelectorAll("svg.i use"));
  check("every mark on the record resolves to a symbol in the sprite",
        marks.length > 0 && marks.every((u) => document.querySelector(u.getAttribute("href"))?.tagName === "symbol"),
        marks.map((u) => u.getAttribute("href")).join(" "));
  check("and each is actually drawn, not collapsed to nothing",
        marks.every((u) => u.ownerSVGElement.getBoundingClientRect().width >= 16),
        marks.map((u) => u.ownerSVGElement.getBoundingClientRect().width).join(","));
  check("none of them is announced: the word beside it carries the meaning",
        marks.every((u) => u.ownerSVGElement.getAttribute("aria-hidden") === "true"));

  const chips = Array.from(document.querySelectorAll(".flags-set .chip"));
  check("each handling chip keeps its word and gains the label's own glyph",
        chips.length === 3 && chips.every((c) => c.querySelector("svg.i use") && c.textContent.trim() !== ""),
        chips.map((c) => c.textContent.trim()).join(","));
  // The `on` state is white on the signal colour; the glyph is currentColor,
  // so it goes white with the word. The colour says the state, never the icon.
  check("the glyph takes the chip's colour, whichever state it is in",
        chips.every((c) => getComputedStyle(c.querySelector("svg.i")).fill === getComputedStyle(c).color),
        chips.map((c) => `${getComputedStyle(c.querySelector("svg.i")).fill} vs ${getComputedStyle(c).color}`).join(" | "));
  const badges = Array.from(document.querySelectorAll(".flags .flag"));
  const raised = ["fragile", "open_first", "heavy"].filter((k) => (record.box || record)[k]);
  check("one badge above the summary per raised flag, each a word and a glyph",
        badges.length === raised.length
          && badges.every((b) => b.querySelector("svg.i use") && b.textContent.trim() !== ""),
        `${badges.map((b) => b.textContent.trim()).join(",")} for ${raised.join(",") || "no flags"}`);

  check("the whole visit wrote nothing", writes.length === 0, writes.join("; "));
  window.fetch = realFetch;

  return results;
};

// The record as it is read: `#/b/CODE`, where nothing saves. The pictures of
// the box and of what is inside it are one field near the top, the rest is
// four label-and-value lines, and there is no control to brush.
const IN_VIEW = async () => {
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
  const open = async (hash, ready) => {
    location.hash = hash;
    await wait(() => $(ready) && !$(ready).hidden, `${hash} to draw`);
    await new Promise((r) => setTimeout(r, 200));
  };

  const rows = await fetch("/api/boxes?limit=100").then((r) => r.json());
  const record = rows.find((b) => b.cover_photo_id) || rows[0];
  const full = await fetch(`/api/boxes/${record.code}`).then((r) => r.json());
  const items = await fetch(`/api/boxes/${record.code}/items`).then((r) => r.json());

  // Everything from here is counted; none of it may write.
  const writes = [];
  const realFetch = window.fetch;
  window.fetch = (url, options = {}) => {
    if ((options.method || "GET") !== "GET") writes.push(`${options.method} ${url}`);
    return realFetch(url, options);
  };

  await open(`#/b/${record.code}`, "#edit");
  check("a record opens as a sheet to read, headed by its code",
        $("h1.code")?.textContent === record.code, $("h1.code")?.textContent);
  check("with an Edit button, which is the way to the page that saves",
        $("#edit")?.getAttribute("href") === `#/b/${record.code}/edit`, $("#edit")?.getAttribute("href"));
  check("and nothing on it to brush: no field, no picker, no tick box",
        document.querySelectorAll("#app input, #app textarea, #app select").length === 0,
        [...document.querySelectorAll("#app input, #app textarea, #app select")].map((f) => f.name || f.type).join(","));

  const sheet = $("#sheet");
  const facts = $("#facts");
  if (full.children?.length || (await fetch(`/api/boxes/${record.code}/photos`).then((r) => r.json())).length) {
    check("the pictures are one field, above the facts",
          sheet && !sheet.hidden && sheet.getBoundingClientRect().bottom <= facts.getBoundingClientRect().top + 1,
          `${Math.round(sheet?.getBoundingClientRect().bottom)} vs ${Math.round(facts.getBoundingClientRect().top)}`);
    check("the photos and the covers are in the same field, nothing between them",
          Boolean(sheet?.querySelector("#view-photos")) && Boolean(sheet?.querySelector("#view-inside")));
  }
  const strip = [...document.querySelectorAll("#view-photos .tile")];
  if (strip.length) {
    const square = strip[0].querySelector(".pic").getBoundingClientRect();
    check("a photo is a square tile, and the sheet scrolls rather than wrapping",
          Math.abs(square.width - square.height) <= 1 && getComputedStyle($("#view-photos")).overflowX === "auto",
          `${Math.round(square.width)}x${Math.round(square.height)}`);
  }

  const facts_ = [...document.querySelectorAll("#facts .fact")].map((f) => f.querySelector("dt").textContent);
  check("the facts are label and value, in order, with no headings between them",
        facts_[0] === "Status" && facts_.includes("What") && facts_.length <= 5, facts_.join(" | "));
  check("the status is set as a word", $("#facts .pill")?.textContent.length > 0, $("#facts .pill")?.textContent);
  const contents = [...document.querySelectorAll("#facts .fact")]
    .find((f) => f.querySelector("dt").textContent === "Contents");
  if (contents && items.length) {
    check("the contents line counts what is listed, then names a few",
          contents.textContent.includes(`${items.length} item`), contents.textContent.trim());
  }

  // A tap on a photo opens the viewer, which is the same one the editor uses.
  if (strip.length) {
    strip[0].click();
    await wait(() => $("dialog.viewer"), "the viewer");
    check("a photo opens the viewer, over the sheet", Boolean($("dialog.viewer[open]")));
    $("dialog.viewer").close();
    await wait(() => !$("dialog.viewer"), "the viewer to close");
  }

  // Every mark drawn here must resolve: a <use> at a missing symbol draws
  // nothing at all, in silence.
  const marks = [...document.querySelectorAll("#app svg.i use")].map((u) => u.getAttribute("href"));
  check("every mark on the sheet resolves to a symbol that exists",
        marks.length === 0 || marks.every((href) => document.querySelector(href)), marks.join(" "));

  check("reading a record wrote nothing", writes.length === 0, writes.join("; "));
  window.fetch = realFetch;
  return results;
};

// Things inside things, wherever this server has some: a record holding
// others, one inside another, and a single thing. Found through the API, so
// this runs against the throwaway (browser_checks.sh seeds them) and skips
// with a note where there are none. Read-only.
const IN_NESTED = async () => {
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
  const open = async (hash, ready) => {
    location.hash = hash;
    await wait(() => $(ready) && !$(ready).hidden, `${hash} to draw`);
    await new Promise((r) => setTimeout(r, 150));
  };
  const rows = await fetch("/api/boxes?limit=100").then((r) => r.json());
  const holder = rows.find((b) => b.child_count > 0);
  if (!holder) {
    check("nesting: nothing on this server has things inside it, so those checks did not run", true);
    return results;
  }

  await open("#/", "#boxlist");
  const holderRow = $(`#boxlist li[data-key="${holder.code}"]`);
  check("the list says how much is inside a container",
        holderRow?.querySelector(".in").textContent === `${holder.child_count} inside`, holderRow?.querySelector(".in")?.textContent);
  check("and nothing on a row with nothing inside",
        Array.from(document.querySelectorAll("#boxlist li")).every((li) => (li.querySelector(".in").textContent !== "")
          === (rows.find((b) => b.code === li.dataset.key)?.child_count > 0)));
  check("nested records are not in the top-level list", rows.every((b) => !b.parent_code));
  check("a row with no photo draws what the record is",
        rows.every((b) => $(`#boxlist li[data-key="${b.code}"] .t .tk use`)?.getAttribute("href") === `#i-${b.kind}`),
        rows.map((b) => `${b.kind}:${$(`#boxlist li[data-key="${b.code}"] .t .tk use`)?.getAttribute("href")}`).join(" "));
  const listThumb = Math.round($("#boxlist li .t").getBoundingClientRect().width);

  const full = await fetch(`/api/boxes/${holder.code}`).then((r) => r.json());
  await open(`#/b/${holder.code}/edit`, "#inside");
  check("on the container, each thing inside is a row, and a row that itself holds things says so",
        full.children.every((k) => {
          const row = $(`#inside li[data-key="${k.code}"]`);
          return row && row.querySelector(".in").textContent === (k.child_count ? `${k.child_count} inside` : "");
        }), Array.from(document.querySelectorAll("#inside li .in")).map((i) => i.textContent).join(","));
  // Measured, because --thumb is a token the nested list overrides, and a typo
  // there silently falls back to the list's own size.
  const insideThumb = Math.round($("#inside li .t").getBoundingClientRect().width);
  check("a row inside a container is drawn half again the size of a list row",
        insideThumb === Math.round(listThumb * 1.5), `${insideThumb} vs ${listThumb}`);
  check("and the kind's mark in it grows to match, on whole pixels",
        Math.round($("#inside li .t .tk").getBoundingClientRect().width) === 40,
        String($("#inside li .t .tk")?.getBoundingClientRect().width));
  check("Delete is not offered while things are inside; why is said instead",
        $("#delete-row").hidden && /Move the .*inside it out first/.test($("#delete-blocked").textContent), $("#delete-blocked")?.textContent);
  const singles = Array.from(document.querySelectorAll('.seg[data-name="kind"] input:disabled')).map((r) => r.value).sort();
  check("the kinds that hold nothing are greyed out, with a reason",
        JSON.stringify(singles) === JSON.stringify(["furniture", "item"]) && /move them out/.test($(".seg-why").textContent),
        `${singles} / ${$(".seg-why")?.textContent}`);
  check("and cannot take the focus", (() => { const r = $('.seg[data-name="kind"] input:disabled'); r.focus(); return document.activeElement !== r; })());

  const child = full.children[0];
  await open(`#/b/${child.code}/edit`, "#trail");
  check("a nested record shows the way out above its code, each step a link",
        $("#trail a")?.getAttribute("href") === `#/b/${holder.code}` && $("#trail").textContent.trim().endsWith("this")
          && $("#trail").compareDocumentPosition($("h1.code")) & Node.DOCUMENT_POSITION_FOLLOWING,
        $("#trail")?.innerText);
  check("and says what it is inside, as a link", $("#inside-of a")?.getAttribute("href") === `#/b/${holder.code}`, $("#inside-of")?.textContent);
  check("with a way to take it out", !$("#container-out").hidden);
  const deep = full.children.find((k) => k.child_count > 0);
  if (deep) {
    const grand = (await fetch(`/api/boxes/${deep.code}`).then((r) => r.json())).children[0];
    await open(`#/b/${grand.code}/edit`, "#trail");
    check("two levels down the breadcrumb has both steps, outermost first",
          Array.from(document.querySelectorAll("#trail a")).map((a) => a.textContent).join(" › ") === `${holder.code} › ${deep.code}`,
          $("#trail")?.innerText);
  }

  const single = full.children.find((k) => k.kind === "item" || k.kind === "furniture");
  if (single) {
    await open(`#/b/${single.code}/edit`, "#summary-form");
    check("a single thing has no 'Inside this' section at all", !$("#inside-section"));
    check("but can still be put inside something", Boolean($("#container-code")));
  }

  // Searched for by code, so this runs against whatever nesting the server has.
  const inner = full.children[0];
  if (inner) {
    const query = encodeURIComponent(inner.code);
    const found = await fetch(`/api/search?q=${query}`).then((r) => r.json());
    const context = found.find((b) => b.code === holder.code && b.matched === false);
    check("search brings back the container a match is inside",
          Boolean(context), found.map((b) => `${b.code}:${b.matched}`).join(" "));
    await open(`#/search/${query}`, "#boxlist");
    const keys = Array.from(document.querySelectorAll("#boxlist li")).map((li) => li.dataset.key);
    const inset = (code) => {
      const link = $(`#boxlist li[data-key="${code}"] > a`);
      return link ? parseFloat(getComputedStyle(link).paddingLeft) : -1;
    };
    check("the container is drawn above what was found inside it",
          keys.includes(holder.code) && keys.indexOf(holder.code) < keys.indexOf(inner.code), keys.join(" "));
    check("and what was found inside it is indented under it",
          inset(inner.code) > inset(holder.code), `${inset(holder.code)} -> ${inset(inner.code)}`);
    if (context) {
      check("a container that did not itself match is marked as context",
            $(`#boxlist li[data-key="${holder.code}"]`)?.hasAttribute("data-context"));
    }
    check("no row says 'in B-xxxx' any more", !$("#boxlist .at"));
    check("a row inside something shows no packing status",
          $(`#boxlist li[data-key="${inner.code}"] .st`)?.textContent === "",
          $(`#boxlist li[data-key="${inner.code}"] .w`)?.innerText);
    check("while a top-level row still has one",
          $(`#boxlist li[data-key="${holder.code}"] .st`)?.textContent !== "",
          $(`#boxlist li[data-key="${holder.code}"] .w`)?.innerText);
  }
  return results;
};

// Settings, for its one section that is arithmetic rather than a form: what
// reading photos has cost, against the cap.
const IN_SETTINGS = async () => {
  const wait = async (test, what) => {
    for (let i = 0; i < 100; i++) {
      if (test()) return;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error(`timed out waiting for ${what}`);
  };
  const results = [];
  const check = (name, passed, detail = "") => results.push([name, Boolean(passed), String(detail)]);

  await wait(() => document.querySelector("[data-reading]"), "the Reading photos section");
  const section = document.querySelector("[data-reading]");
  const spend = await fetch("/api/settings/spend").then((r) => r.json());

  check("Settings says what is reading photos",
        section.textContent.includes(spend.local_model), section.textContent.slice(0, 120));
  // The rule is unit-tested; this checks that the panel renders it. This runs
  // against a throwaway (no key, so "local") and the live service (a key that
  // may work or be refused), so it pins the panel to what /api/settings/spend
  // reports, not to one deployment's answer.
  const { readingWith } = await import("/covers.js");
  const expected = readingWith(spend).state;
  check("the panel draws the state the rule derives from the server's numbers",
        section.dataset.reading === expected,
        `panel=${section.dataset.reading} rule=${expected}`);
  check("and it is one of the states the app knows",
        ["local", "cloud", "failing", "capped"].includes(section.dataset.reading),
        section.dataset.reading);
  check("no part of a key is anywhere on the page",
        !document.body.textContent.includes("sk-ant"));

  // The gauge is a width, so a NaN or an overflow shows up as a bar that is
  // the wrong size rather than as an error.
  const filled = section.querySelector(".gauge span");
  const width = Number.parseFloat(filled?.style.width);
  check("the spend gauge has a real width between none and full",
        Number.isFinite(width) && width >= 0 && width <= 100, filled?.style.width);
  check("the gauge is not as wide as the page",
        filled.getBoundingClientRect().width <= section.getBoundingClientRect().width);

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

  // Rooms read in alphabetical order, in both rows, whatever order the server
  // lists them in.
  for (const name of ["destination_room_id", "source_room_id"]) {
    const labels = [...document.querySelectorAll(`#new input[name="${name}"] + span`)]
      .map((s) => s.textContent.trim());
    const sorted = [...labels].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    check(`the ${name.replace("_room_id", "")} rooms are alphabetical`,
          labels.length > 1 && JSON.stringify(labels) === JSON.stringify(sorted), JSON.stringify(labels));
  }

  // The running version: stamped into the page by the server, so it must match
  // what /health says, and it sits at the right -- above the bar on a phone,
  // in the bar on a desktop -- without taking a tap-sized slot from the menu.
  const shown = document.getElementById("version")?.textContent;
  const health = await (await fetch("/health")).json();
  check("the version on the page is the one running", shown === `v${health.version}`, `${shown} vs ${health.version}`);
  const at = document.getElementById("version").getBoundingClientRect();
  const bar = document.querySelector("nav.bar").getBoundingClientRect();
  const desktop = window.innerWidth >= 46 * 16;
  check(desktop ? "on a desktop it sits in the bar" : "on a phone it sits above the bar, at the right",
        desktop ? (at.top >= bar.top - 1 && at.bottom <= bar.bottom + 1)
                : (at.bottom <= bar.top + 1 && at.right > window.innerWidth / 2),
        JSON.stringify({ at, bar, innerWidth: window.innerWidth }));
  // The bar: a mark over the word on a phone, beside it on a desktop, never
  // instead of it -- "Items" and "New" are not guessable from a glyph.
  const tabs = Array.from(document.querySelectorAll("nav.bar a"));
  check("every tab in the bar keeps its word and wears a mark",
        tabs.length === 4 && tabs.every((a) => a.querySelector("svg.i use") && a.querySelector("span")?.textContent.trim()),
        tabs.map((a) => a.textContent.trim()).join(","));
  check("and what a screen reader hears is still the word",
        tabs.every((a) => a.textContent.trim() === a.querySelector("span").textContent.trim()
                          && a.querySelector("svg.i").getAttribute("aria-hidden") === "true"));
  const mark = tabs[0].querySelector("svg.i").getBoundingClientRect();
  check(desktop ? "the mark is 20px, beside the word" : "the mark is 24px, over the word",
        Math.round(mark.width) === (desktop ? 20 : 24), String(mark.width));
  check("stacking it did not push the word out of the bar",
        tabs.every((a) => a.getBoundingClientRect().bottom <= bar.bottom + 1));
  // Which tab you are on: the stylesheet asks for [aria-current="page"], and
  // an empty aria-current marks nothing.
  const here = tabs.filter((a) => a.getAttribute("aria-current") === "page");
  check("the bar says which page you are on",
        here.length === 1 && here[0].getAttribute("href") === location.hash,
        `at ${location.hash}: ` + tabs.map((a) => `${a.getAttribute("href")}=${a.getAttribute("aria-current")}`).join(" "));
  const other = tabs.find((a) => a !== here[0]);
  check("and it is drawn differently from the others",
        Boolean(here[0]) && getComputedStyle(here[0]).backgroundColor !== getComputedStyle(other).backgroundColor,
        here[0] ? `${getComputedStyle(here[0]).backgroundColor} vs ${getComputedStyle(other).backgroundColor}`
                : "no tab is marked current");

  // A pushbutton row is one control, so only its outside is rounded: the
  // seams where buttons meet stay square. The row wraps on a phone, which is
  // why the rounding lives on the row and not on its first and last button --
  // those are mid-line as often as not.
  const segRow = document.querySelector(".seg-row");
  const segSpans = Array.from(segRow.querySelectorAll("span"));
  check("a pushbutton row is rounded on the outside",
        getComputedStyle(segRow).borderTopLeftRadius !== "0px",
        getComputedStyle(segRow).borderRadius);
  check("and square at every seam between its buttons",
        segSpans.every((s) => getComputedStyle(s).borderTopLeftRadius === "0px"
                           && getComputedStyle(s).borderBottomRightRadius === "0px"),
        segSpans.map((s) => getComputedStyle(s).borderTopLeftRadius).join(","));

  const buttons = Array.from(document.querySelectorAll("#new button[type=submit]"));
  const form = document.getElementById("new");
  const kinds = await fetch("/api/settings/kinds").then((r) => r.json());
  const press = (name, value) => form.querySelector(`.seg[data-name="${name}"] input[value="${value}"]`).labels[0].click();

  const create = document.querySelector("#new #create");
  check("the first button creates and prints the stub",
        buttons[0]?.dataset.print === "stub", buttons[0]?.textContent);
  check("the plain Create prints nothing",
        buttons.includes(create) && !create.hasAttribute("data-print"), create?.textContent);
  check("the last button is the one that prints the full label",
        buttons.at(-1)?.dataset.print === "label", buttons.at(-1)?.textContent);
  check("Create names the kind it makes",
        /^Create \w+$/.test(create.textContent), create.textContent);

  // The same rows of pushbuttons as the record page. Nothing here writes: the
  // form saves nothing until a Create is pressed, and none is.
  check("the form's pickers are pushbutton rows, not dropdowns",
        ["kind", "size", "destination_room_id", "source_room_id"].every((n) => form.querySelector(`.seg[data-name="${n}"]`))
          && !form.querySelector("select"));
  const sized = kinds.find((k) => k.sizes.length);
  const single = kinds.find((k) => !k.sizes.length);
  const sizeRow = form.querySelector('.seg[data-name="size"]');
  if (sized && single) {
    press("kind", sized.kind);
    check("changing the kind renames Create", create.textContent === `Create ${sized.label.toLowerCase()}`, create.textContent);
    check("a container offers a size", !sizeRow.hidden && !sizeRow.disabled);
    press("size", sized.sizes[0]);
    check("a chosen size is in what the form would send", new FormData(form).get("size") === sized.sizes[0]);
    press("size", sized.sizes[0]);
    check("pressing it again clears it", new FormData(form).get("size") === null
          && sizeRow.querySelector(".seg-said").textContent === "No size");
    press("size", sized.sizes[0]);
    press("kind", single.kind);
    check("a single thing offers no size", sizeRow.hidden && sizeRow.disabled);
    check("and a size chosen before the kind changed would not be sent", new FormData(form).get("size") === null);
    check("Create follows", create.textContent === `Create ${single.label.toLowerCase()}`, create.textContent);
  }
  const room = form.querySelector('.seg[data-name="destination_room_id"] input');
  if (room) {
    room.labels[0].click();
    check("a room can be chosen", new FormData(form).get("destination_room_id") === room.value);
    room.labels[0].click();
    check("and un-chosen, which sends no room at all", new FormData(form).get("destination_room_id") === null
          && form.querySelector('.seg[data-name="destination_room_id"] .seg-said').textContent === "Not decided yet");
  }
  // Return on a pushbutton must not make a record (a barcode reader ends on one).
  const before = location.hash;
  const radio = form.querySelector('.seg[data-name="kind"] input:checked');
  radio.focus();
  const notStopped = radio.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 300));
  check("Return on a pushbutton creates nothing", !notStopped && location.hash === before);
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
  // A headless page does not have the focus, and an unfocused page fires no
  // focus or blur events. Blur is what saves a rename.
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });

  await send("Page.enable");
  await send("Page.navigate", { url: `${base}/#/b/${encodeURIComponent(code)}/edit` });
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

  const third = await send("Runtime.evaluate", {
    expression: `(${IN_NESTED.toString()})()`, awaitPromise: true, returnByValue: true,
  });
  if (third.result?.exceptionDetails) {
    throw new Error(third.result.exceptionDetails.exception?.description || "nesting script failed");
  }
  results.push(...third.result.result.value);
  await send("Page.navigate", { url: `${base}/#/settings` });
  await sleep(400);
  const fourth = await send("Runtime.evaluate", {
    expression: `(${IN_SETTINGS.toString()})()`, awaitPromise: true, returnByValue: true,
  });
  if (fourth.result?.exceptionDetails) {
    throw new Error(fourth.result.exceptionDetails.exception?.description || "settings script failed");
  }
  results.push(...fourth.result.result.value);

  await send("Page.navigate", { url: `${base}/#/b/${encodeURIComponent(code)}` });
  await sleep(500);
  const fifth = await send("Runtime.evaluate", {
    expression: `(${IN_VIEW.toString()})()`, awaitPromise: true, returnByValue: true,
  });
  if (fifth.result?.exceptionDetails) {
    throw new Error(fifth.result.exceptionDetails.exception?.description || "view script failed");
  }
  results.push(...fifth.result.result.value);

  // The browse list at a phone's width. A narrow --window-size does not narrow
  // the layout, so only an emulated viewport can say whether anything
  // overflows. Read-only, so it runs against the live service too.
  for (const width of [320, 400]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 700, deviceScaleFactor: 0, mobile: true });
    await send("Page.navigate", { url: `${base}/#/` });
    await sleep(700);
    const listAt = await send("Runtime.evaluate", { returnByValue: true, expression: `(() => {
      const rows = [...document.querySelectorAll("#boxlist li")];
      const doc = document.documentElement;
      return {
        rows: rows.length,
        overflow: doc.scrollWidth - doc.clientWidth,
        right: rows.length ? Math.max(...rows.map((li) => li.querySelector(".w")?.getBoundingClientRect().right || 0)) : 0,
        bg: rows.slice(0, 3).map((li) => getComputedStyle(li.querySelector("a")).backgroundColor),
        rule: rows.length ? getComputedStyle(rows[0].querySelector("a")).borderBottomWidth : "0px",
      };
    })()` });
    const got = listAt.result.result.value;
    results.push([`the list at ${width}px: nothing runs off the right-hand edge`,
      got.rows > 0 && got.overflow <= 0 && got.right <= width,
      `rows ${got.rows}, overflow ${got.overflow}, right edge ${Math.round(got.right)}`]);
    results.push([`the list at ${width}px: rows alternate backgrounds, with no rule`,
      got.rows < 3 || (got.bg[0] !== got.bg[1] && got.bg[0] === got.bg[2] && got.rule === "0px"),
      `${got.bg.join(" | ")} rule ${got.rule}`]);
  }
  await send("Emulation.clearDeviceMetricsOverride");

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
