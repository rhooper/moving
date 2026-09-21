# Icons: what each one means, and where they ship

One family with the home mark: a 16-unit grid, one evenodd path per icon,
2-unit strokes, solid masses, holes only where they mean something.
`fill="currentColor"`, so one file serves both themes. No radius, accent, font
or stroke attributes. `set/*.svg` are the individual files; `sprite.svg` holds
them as `<symbol id="i-…">`, and `web/index.html` inlines exactly that sprite (a
test compares the two). `proof.html` shows the set at every size in both themes.

## The set

- **Nav**: `items` (rows of a thumbnail and a line), `scan` (viewfinder corners
  round a QR finder pattern), `new` (plus), `settings` (three sliders).
- **Kinds**: `box` (flaps up), `parts` (three small bins over two wide ones --
  irregular, since a regular grid reads as a window), `tub` (wide, low,
  tapered, lid hovering -- taller than wide it is a bin), `crate` (posts and
  three boards), `bag` (by its handle -- wider than tall, or it is a padlock),
  `item` (a tag on a string: a single thing that holds nothing), `furniture` (a
  chair in profile).
- **Handling flags**, the printed label's own glyphs on this grid: `fragile`
  (the broken goblet), `heavy` (the weight, its loop squared), `open-first` (a
  1 -- the label draws a double rule instead, so this is the one invented mark).
- **Variants, drawn and not shipped**: `scan-qr` (softer at 16), `crate-lid`
  (at 16 it is two eyes), `open-first-rule` (the label's double rule; at 16 a
  picture frame).

## Where they ship: three places only

1. **The nav bar, always with the word** -- stacked over it at 24 px on a phone,
   beside it at 20 px on a desktop. "Items" and "New" are not guessable from a
   list glyph and a plus.
2. **A list row's empty thumbnail**, showing the record's kind, under the photo.
   Not in the kind picker: tub, crate and bag are not certain enough at 24 px
   without their words.
3. **The handling flags**, in the toggles and badges, because the printed label
   carries them and the app mirrors the label. The colour is the toggle's
   state, never the icon's.

## Tried and turned down -- re-adding one is a regression

- **The status track**: open and unpacked are the same open box, delivered is
  the home mark's house, loaded is a blob at 16, and the track already says
  done / now / next in words.
- **Nesting**: arrows into and out of a tray read as download and upload; a box
  in a box is a finder pattern. The breadcrumb separator (›) stays typographic.
- **Section headings**: decoration on headings that already say what they are.
- **Delete**: a bin icon invites the tap the danger section exists to prevent.
- **Look closer**: a magnifier promises zoom; it is a slower, careful read.
- **The size row**: there is no honest picture of "medium".

## How they hold up, and sizes

Crisp at 16, 24 and 32: items, scan, new, settings, crate, bag, tub,
open-first. Soft at 16 (diagonals): box, furniture, fragile, heavy; `parts` and
`crate` are soft at 24, where odd coordinates land on half pixels. The weakest
is `item`, an arrow with a dot at 16: a loose thing has no silhouette of its
own, and the tag is the honest generic.

Multiples of 8 land every edge on a pixel: 16 (chips), 24 (the phone bar), 32,
and 40 (the kind in a container's rows, not the arithmetic 39). 20 (the desktop
bar) and 26 (the list thumbnail) are off the grid but fine: the anti-aliasing
is symmetric. 28 is the unkind size, as with the home mark.
