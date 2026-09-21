// A row of joined pushbuttons -- [ Box | Tub | Crate ] -- in place of a
// dropdown. Underneath is a real radio group (radios inside <label>s drawn as
// buttons, in a <fieldset>), so a named group, arrow keys, FormData and the
// input/change events autosave listens for come free. What this adds: an
// optional row clears on a second press. Built with textContent, and nothing
// touches `document` at import time, so it is tested under node.

// `cleared` only for the second press on the selected button of an optional
// row, the one case the browser does nothing for. A required row ignores it.
export function pressed(current, value, { optional = false } = {}) {
  const now = String(current ?? "");
  const next = String(value ?? "");
  if (optional && now !== "" && now === next) return { value: "", cleared: true };
  return { value: next, cleared: false };
}

// The line under an optional row: what nothing chosen means, or how to
// un-choose. Always one or the other, so its appearing never moves the page.
export function hintFor(value, empty) {
  return String(value ?? "") === "" ? empty : "Tap it again to clear";
}

// The selected value, or "" for none.
export function chosen(group) {
  const radio = Array.from(group.querySelectorAll("input[type=radio]")).find((r) => r.checked);
  return radio ? radio.value : "";
}

// Shows `value` ("" for none) without firing events: for a value the caller
// already knows about, such as what Undo returned.
export function choose(group, value) {
  const wanted = String(value ?? "");
  for (const radio of group.querySelectorAll("input[type=radio]")) {
    radio.checked = wanted !== "" && radio.value === wanted;
  }
  settle(group);
}

function settle(group) {
  group.dataset.value = chosen(group);
  const hint = group.querySelector(".seg-hint");
  if (!hint) return;
  const said = hint.querySelector(".seg-said");
  const text = hintFor(group.dataset.value, group.dataset.empty || "");
  if (said.textContent !== text) said.textContent = text;
  // Visually hidden, not display:none, which would drop it from the description.
  hint.querySelector(".vh").textContent =
    group.dataset.value === "" ? "" : " With a keyboard, press Space on it.";
}

// Greys out buttons with a reason under the row; empty `values` lifts it. Real
// `disabled`, so arrow keys skip them and a screen reader says so.
export function restrict(group, values, why) {
  const off = new Set((values || []).map(String));
  for (const radio of group.querySelectorAll("input[type=radio]")) {
    radio.disabled = off.has(radio.value);
    radio.closest("label").classList.toggle("off", radio.disabled);
  }
  let note = group.querySelector(".seg-why");
  if (!note) {
    note = document.createElement("p");
    note.className = "meta seg-why";
    group.append(note);
  }
  note.textContent = off.size ? why : "";
  note.hidden = !off.size;
}

// `options` are `{ value, label }`; `value` starts selected ("" or null for
// none). An `optional` row clears on a second press, and its hint reads `empty`
// while nothing is chosen. FormData has the value (no entry for none), and a
// selection or clearing bubbles "input" then "change", as a <select> does.
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
    // The live-refresh hold's baseline.
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

  // The browser fires nothing for a press that changed nothing; this is a
  // change, so it is announced like one.
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
  // Chrome sends no click for Space on a radio already chosen, so the second
  // key press is handled here, and stopped, so a browser that does click does
  // not clear twice.
  group.addEventListener("keydown", (event) => {
    const radio = event.target;
    if (event.key !== " " || radio.type !== "radio" || !radio.checked) return;
    if (!pressed(group.dataset.value, radio.value, { optional }).cleared) return;
    event.preventDefault();
    clear(radio);
  });
  // Runs after the click handler, so a press is judged against the selection
  // as it was before it.
  group.addEventListener("change", () => settle(group));
  return group;
}
