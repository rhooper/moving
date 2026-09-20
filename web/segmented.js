// A row of joined pushbuttons -- [ Box | Tub | Crate ] -- in place of a
// dropdown: every choice is on the screen and one tap away, which is what a
// thumb wants and a <select> is not.
//
// Underneath it is a real radio group: native <input type="radio"> inside a
// <fieldset>, each wrapped in the <label> that is drawn as the button. That
// buys, for nothing: a group with a name for a screen reader, arrow keys,
// FormData, and the input/change events the record page's autosave already
// listens for. The one thing a radio will not do is un-check when pressed
// again, and an optional choice (a room, a size) has to be clearable now that
// there is no empty <option> to pick -- so that is what this adds.
//
// Built with DOM calls and textContent, like the list rows in app.js: nothing
// is interpolated into markup, so there is nothing to escape. Nothing here
// touches `document` at import time, so the decisions below load under node
// and are tested in tests/segmented.test.mjs.

/**
 * What pressing the button for `value` does to a row whose selection is
 * `current`. `cleared` is true only for the second press on the selected
 * button of an optional row -- the one case the browser does nothing for.
 *
 * A row that must have a value (the kind) ignores that press: there is no such
 * thing as a record that is not anything.
 */
export function pressed(current, value, { optional = false } = {}) {
  const now = String(current ?? "");
  const next = String(value ?? "");
  if (optional && now !== "" && now === next) return { value: "", cleared: true };
  return { value: next, cleared: false };
}

/**
 * The line under an optional row. While nothing is chosen it says what that
 * means ("Not decided yet") -- the empty <option> used to; once something is,
 * it says how to un-choose it, which nobody would guess. Always one or the
 * other, so the line is always there and neither appearing moves the page.
 */
export function hintFor(value, empty) {
  return String(value ?? "") === "" ? empty : "Tap it again to clear";
}

/** The selected value of a row built by `segmented`, or "" for none. */
export function chosen(group) {
  const radio = Array.from(group.querySelectorAll("input[type=radio]")).find((r) => r.checked);
  return radio ? radio.value : "";
}

/**
 * Show `value` as the selection ("" for none) without announcing it as an
 * edit: no events fire. For putting back what Undo returned, or text the
 * server never got -- the caller already knows about those.
 */
export function choose(group, value) {
  const wanted = String(value ?? "");
  for (const radio of group.querySelectorAll("input[type=radio]")) {
    radio.checked = wanted !== "" && radio.value === wanted;
  }
  settle(group);
}

// Bring the row's own record of its selection, and the line under it, into
// step with the radios.
function settle(group) {
  group.dataset.value = chosen(group);
  const hint = group.querySelector(".seg-hint");
  if (!hint) return;
  const said = hint.querySelector(".seg-said");
  const text = hintFor(group.dataset.value, group.dataset.empty || "");
  if (said.textContent !== text) said.textContent = text;
  // Not display:none, which would drop it from the description: this half of
  // the line is for somebody who cannot tap.
  hint.querySelector(".vh").textContent =
    group.dataset.value === "" ? "" : " With a keyboard, press Space on it.";
}

/**
 * Build a row.
 *
 * `options` are `{ value, label }`; `value` is the selection to start with
 * ("" or null for none); `optional` rows clear on a second press and carry the
 * line described above, which reads `empty` while nothing is chosen.
 *
 * Returns the <fieldset>. Its radios carry `name`, so a form holding it needs
 * nothing else: FormData has the value (or no entry, for none), and a
 * selection or a clearing bubbles "input" then "change" from a radio, exactly
 * as a <select> did.
 */
export function segmented({ name, legend, options, value = "", optional = false, empty = "" }) {
  const group = document.createElement("fieldset");
  group.className = "seg";
  group.dataset.name = name;
  group.dataset.empty = empty;
  if (optional) group.dataset.optional = "";

  const title = document.createElement("legend");
  title.className = "dlabel";
  title.textContent = legend;
  group.append(title);

  const hintId = `seg-${name}-hint`;
  const row = document.createElement("div");
  row.className = "seg-row";
  const selected = String(value ?? "");
  for (const option of options) {
    const button = document.createElement("label");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = name;
    radio.value = String(option.value);
    radio.checked = selected !== "" && radio.value === selected;
    // What the live-refresh hold compares against to tell "as drawn" from
    // "changed and not yet saved" (see fieldValue in app.js).
    radio.dataset.initial = String(radio.checked);
    if (optional) radio.setAttribute("aria-describedby", hintId);
    const face = document.createElement("span");
    face.textContent = option.label;
    button.append(radio, face);
    row.append(button);
  }
  group.append(row);

  if (optional) {
    const hint = document.createElement("p");
    hint.className = "meta seg-hint";
    hint.id = hintId;
    const said = document.createElement("span");
    said.className = "seg-said";
    const more = document.createElement("span");
    more.className = "vh";
    hint.append(said, more);
    group.append(hint);
  }
  settle(group);

  // Un-choose `radio`. The browser fires nothing for a press that changed
  // nothing, and this is a change: say so the way a real one is said.
  const clear = (radio) => {
    radio.checked = false;
    settle(group);
    radio.dispatchEvent(new Event("input", { bubbles: true }));
    radio.dispatchEvent(new Event("change", { bubbles: true }));
  };
  // A tap on a label arrives here twice -- once on the label, once as the
  // click the browser forwards to its radio. Only the radio's counts.
  group.addEventListener("click", (event) => {
    const radio = event.target;
    if (radio.type !== "radio") return;
    if (pressed(group.dataset.value, radio.value, { optional }).cleared) clear(radio);
  });
  // The keyboard's second press. It cannot ride on the click above: Chrome
  // deliberately sends no click for Space on a radio that is already chosen
  // (found by pressing it, in autosave_check.mjs). Handled on the way down and
  // stopped there, so a browser that *does* click does not then clear twice.
  group.addEventListener("keydown", (event) => {
    const radio = event.target;
    if (event.key !== " " || radio.type !== "radio" || !radio.checked) return;
    if (!pressed(group.dataset.value, radio.value, { optional }).cleared) return;
    event.preventDefault();
    clear(radio);
  });
  // Every real selection ends here, arrow keys included. It runs after the
  // click above, so the press is always judged against the selection as it
  // was before it.
  group.addEventListener("change", () => settle(group));
  return group;
}
