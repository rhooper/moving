# Icons — what each one means, and which to ship

One family with the home mark: a 16-unit grid, one evenodd path per icon,
2-unit strokes (the home mark's ring), solid masses, holes only where they
mean something. `fill="currentColor"`, so one file serves both themes. No
radius, no accent, no font, no stroke attributes. Subpaths never overlap, so
the drawing is identical under nonzero — evenodd is used only for the
deliberate knockouts.

`set/*.svg` are the individual files; `sprite.svg` holds the same paths as
`<symbol id="i-…">` for inlining once and `<svg><use href="#i-box"/></svg>`.
The sprite root carries `fill="currentColor"` and `style="display:none"`.

## The set

Nav

- `items` — the list: rows of a thumbnail and a line, which is what the Items page is.
- `scan` — viewfinder corners around a QR finder pattern: point the camera at a label.
- `new` — plus. Make a record.
- `settings` — three sliders with square knobs: things you set.
- `scan-qr` (variant) — scan as a small QR code, three finders and some data. Same meaning, more "QR", less "action"; softer at 16.

Kinds

- `box` — a cardboard box with its flaps up.
- `tub` — a plastic tub: wide, low, tapered, lid off and hovering. Drawn wider than tall on purpose; taller than wide it is a bin.
- `crate` — a slatted crate: posts and three boards.
- `bag` — a bag by its handle. Body wider than tall on purpose; square it is a padlock.
- `item` — a tag on a string: a single thing that carries a label and holds nothing.
- `furniture` — a chair, in profile.
- `crate-lid` (variant) — a lidded moving crate with grips notched into the sides. Right if the crates are the plastic rental kind; at 16 it is two eyes.

Handling flags (the label's own glyphs, redrawn on this grid)

- `fragile` — the label's broken goblet.
- `heavy` — the label's weight: tapered body, handle loop. The loop is square here where the label's is an arc; side by side they read as the same object.
- `open-first` — a 1: open this one first. The label has no glyph for this (it uses a double rule round the whole label), so this is the one invented mark.
- `open-first-rule` (variant) — the label's double rule, drawn round a small label. Faithful, but at 16 it is a picture frame and means nothing without the label beside it.

## Recommendation: which of 1–6 to ship

1. **Nav — ship, always with the word.** Stacked icon over word at 24 px on
   the phone bar (the bar grows from 48 to 56 px); icon beside word at 20 px
   on desktop. Never icons alone: "Items" and "New" are not guessable from a
   list glyph and a plus, and the bar has room for the words.
2. **Kinds — ship, in the list row's empty thumbnail first.** That frame
   already shows a placeholder (the same open box on every row); showing the
   record's kind there costs nothing and says something. In the pushbutton
   row, icon-and-word is fine and icons-only is not: tub, crate and bag are
   not certain enough at 24 px to stand without their words.
3. **Flags — ship.** This is the strongest case in the brief: the printed
   chip already carries the goblet and the weight, and the app is meant to
   mirror the label. Use them in the `.chip` toggles (off: muted; on: white on
   the signal colour, which is the chip's own state, not the icon's) and in
   the `.flag` badges on the record page. Choose `open-first` (the 1) unless
   fidelity to the label's double rule matters more than reading at 16.
4. **Status track — do not ship.** Tried in the real track (see the proof):
   open and unpacked are the same open box, delivered is the home mark's
   house, loaded is a blob at 16, and the track already draws progress as
   done / now / next with the words. Icons there add a second thing to read.
5. **Nesting — do not ship.** "Put it inside" / "Take it out" drawn as arrows
   into and out of a tray read as download and upload; "contains" drawn as a
   box in a box is a finder pattern; the breadcrumb separator is typographic
   (›) and should stay so. The sentences on those buttons are the icon.
6. **Mistakes to iconify, beyond 4 and 5:** the section headings (What is in
   it, Where it is going, Photos, Label, Delete) — decoration on headings that
   already say what they are; Delete — a bin icon invites the tap the danger
   section exists to prevent; "Look closer" — a magnifier promises zoom, and
   it is a slower model read; the size row (small / medium / large / XL) —
   there is no honest picture of "medium".

## How they hold up (honest)

- Crisp at 16, 24 and 32: items, scan, new, settings, crate, bag, tub,
  open-first, open-first-rule.
- Good at 24 and 32, soft at 16 (diagonals): box, furniture, fragile,
  heavy. All still recognisable at 16 next to their word or in a 44 px frame.
- Weakest: `item` (the tag). At 16 it is an arrow with a dot; at 24 and in
  the list thumbnail it is a tag. A single loose thing has no silhouette of
  its own, and the tag is the honest generic. If it bothers, use the word.

## Sizes to use

Multiples of 8 land every edge on a pixel: 16 (chip text), 24 (phone bar,
stacked), 32. 20 px (desktop bar) and 26 px (list thumbnail, today's
placeholder size) are off-grid and fine — the anti-aliasing is symmetric.
28 is the unkind size, as with the home mark.
