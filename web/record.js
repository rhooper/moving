// The read-only record sheet: what it shows and in what order.
//
// Asked for as "a view mode with an edit button for viewing boxes. make it
// compact, and gather photos and subitem covers near each other, near the
// top." Somebody has just scanned a sealed box and is holding it, and the
// fastest answer to "what is this?" is a picture of it and of the things
// inside it. So the pictures come first, together, and what the editor spreads
// over eight headed sections of live-saving controls becomes four lines.
//
// Kept out of app.js (which touches the DOM on import) so it can be tested.

/** Cells in a grid before the last one becomes a way in to the rest. B-0015
 *  holds twenty tubs and fifteen photographs: a wall of either buries the
 *  facts under it, and a screenful is what the question "what is in here?"
 *  actually needs. The same cap for both, so the two halves of the sheet
 *  behave alike. */
export const COVERS = 8;

/** Names on the contents line before it trails off. */
export const NAMES = 4;

/**
 * Which tiles to draw, and how many are left over. Used for both halves of the
 * sheet: the record's own photographs and the covers of what is inside it.
 *
 * Over the cap the last cell is spent on the way in to the rest rather than on
 * one more tile, so the grid stays one block and nothing is hidden without
 * saying so.
 */
export function tilesFor(list_, { shown = COVERS, all = false } = {}) {
  const list = list_ || [];
  if (all || list.length <= shown) return { tiles: [...list], more: 0 };
  return { tiles: list.slice(0, shown - 1), more: list.length - (shown - 1) };
}

/**
 * The contents line: how many, then a few names, and whether there is a list
 * worth opening.
 *
 * The count is the record's *own* items -- what is nested has its own tile
 * above, and asking the server for a whole subtree to draw one line would slow
 * every record down for a list most people never open. A record that lists
 * nothing itself can still be worth opening when something inside it lists
 * something.
 */
export function contentsLine(items, { children = [], names = NAMES } = {}) {
  const list = items || [];
  const inside = (children || []).length;
  if (!list.length) {
    return {
      count: 0,
      value: inside ? "Nothing listed on this one" : "Nothing listed",
      detail: "",
      expandable: inside > 0,
    };
  }
  const shown = list.slice(0, names).map((item) => item.name);
  return {
    count: list.length,
    value: `${list.length} item${list.length === 1 ? "" : "s"}`,
    detail: `${shown.join(", ")}${list.length > shown.length ? "…" : ""}`,
    expandable: true,
  };
}

const sentence = (text) => {
  const said = String(text || "").trim();
  return said ? said[0].toUpperCase() + said.slice(1) : "";
};

/**
 * The lines under the pictures: label and value, no headings, and only what
 * this record has something to say about.
 *
 * `what` is `rowStatus(box).kind` -- the phrase a list row uses, passed in so
 * that one function still decides how a kind and a size read together and the
 * row and this page cannot drift apart. `contents` is `contentsLine`'s answer,
 * or null for something that holds nothing: "Contents: nothing listed" under a
 * lamp is a question nobody asked.
 */
export function facts(box, { what = "", source = null, contents = null } = {}) {
  const record = box || {};
  const lines = [
    { key: "status", label: "Status", value: sentence(record.status || "open"), pill: true },
  ];
  // Where it is *now* is not where it is going: a box for the kitchen may be
  // on the truck. It sits next to the status, which it qualifies.
  const now = String(record.current_location || "").trim();
  if (now) lines.push({ key: "now", label: "Right now", value: now });

  lines.push({ key: "what", label: "What", value: sentence(what) });
  if (contents) {
    lines.push({
      key: "contents", label: "Contents",
      value: contents.value, detail: contents.detail, expandable: contents.expandable,
    });
  }

  const place = String(record.source_location || "").trim();
  const from = [source?.name, place].filter(Boolean).join(" — ");
  if (from) lines.push({ key: "from", label: "Packed from", value: from });
  return lines;
}

/**
 * The expanded contents, as the server grouped them: the record's own items
 * first, then each record nested inside it.
 *
 * A heading is the record's code, and it is what makes an item's place
 * unambiguous -- so every group carries one, including the record's own, and a
 * group of one item carries one too. With nothing nested there is only one
 * group and nowhere else an item could be, so the heading would be noise.
 * A record that lists nothing is not an empty heading.
 */
export function expandedGroups(groups) {
  const kept = (groups || []).filter((group) => (group.items || []).length);
  const heading = kept.length > 1;
  return kept.map((group) => ({ code: group.code, heading, items: group.items }));
}
