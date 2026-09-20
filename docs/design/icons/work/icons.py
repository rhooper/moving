"""The icon set: one source for set/*.svg and sprite.svg. Scratch tool.
Every icon is a single evenodd path on a 16-unit grid, fill=currentColor.
Subpaths never overlap, so the drawing is the same under nonzero; evenodd is
used only for the deliberate knockouts (a hole in a handle, a slot, a crack).
Stroke weight is 2 units -- the home mark's ring -- masses are solid.
Run: python3 icons.py   (writes ../set/*.svg and ../sprite.svg)"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# id -> (meaning, path). Order is the order of the sprite and the proof.
ICONS = {
    # --- nav ---
    "items":    ("The list: rows of a thumbnail and a line, which is what the Items page is.",
                 "M0 0h4v4H0zM6 0h10v4H6zM0 6h4v4H0zM6 6h10v4H6zM0 12h4v4H0zM6 12h10v4H6z"),
    "scan":     ("Viewfinder corners around a QR finder pattern: point the camera at a label.",
                 "M0 0h6v2H2v4H0zM10 0h6v6h-2V2h-4zM0 10h2v4h4v2H0zM14 10h2v6h-6v-2h4zM5 5h6v6H5zM7 7v2h2V7z"),
    "new":      ("Plus. Make a record.",
                 "M7 2h2v5h5v2H9v5H7V9H2V7h5z"),
    "settings": ("Three sliders with square knobs: things you set.",
                 "M2 0h4v4H2zM0 1h2v2H0zM6 1h10v2H6zM10 6h4v4h-4zM0 7h10v2H0zM14 7h2v2h-2zM6 12h4v4H6zM0 13h6v2H0zM10 13h6v2h-6z"),
    # --- kinds ---
    "box":       ("A cardboard box with its flaps up.",
                  "M2 8h12v8H2zM2 8L0 2h2l2 6zM14 8l2-6h-2l-2 6z"),
    "tub":       ("A plastic tub: wide, low, tapered, lid off and hovering. Wider than tall, or it is a bin.",
                  "M0 4h16v2H0zM1 8h14l-1 8H2z"),
    "crate":     ("A slatted crate: posts and three boards.",
                  "M0 2h2v14H0zM14 2h2v14h-2zM2 2h12v3H2zM2 7h12v3H2zM2 12h12v4H2z"),
    "bag":       ("A bag by its handle. The body is wider than it is tall, or it is a padlock.",
                  "M4 0h8v6H4zM6 2v4h4V2zM0 6h16v10H0z"),
    "item":      ("A tag on a string: a single thing that carries a label and holds nothing.",
                  "M2 2h10l4 6-4 6H2zM10 7v2h2V7zM0 7h2v2H0z"),
    "furniture": ("A chair, in profile.",
                  "M2 0h3v16H2zM5 8h9v3H5zM12 11h2v5h-2z"),
    # --- handling flags (the label's own glyphs, on this grid) ---
    "fragile":    ("The label's broken goblet.",
                   "M2 1h12l-4 7H6zM7 8h2v5H7zM4 13h8v2H4zM11 2l-2.6 2.4 1.8 1.6-1.2 1.4 2.8-2.2-1.8-1.6z"),
    "heavy":      ("The label's weight: a tapered body with a handle loop.",
                   "M5 1h6v6H5zM7 3v3h2V3zM5 6h6l3 9H2z"),
    "open-first": ("A 1: open this one first.",
                   "M10 0v16H6V5L2 7V3l4-3z"),
}

# Variants: genuinely close calls, offered beside the primary.
VARIANTS = {
    "scan-qr":         ("Scan, as a small QR code: three finders and some data.",
                        "M0 0h6v6H0zM2 2v2h2V2zM10 0h6v6h-6zM12 2v2h2v-2zM0 10h6v6H0zM2 12v2h2v-2zM8 8h2v2H8zM12 8h2v2h-2zM10 10h2v2h-2zM14 10h2v2h-2zM8 12h2v2H8zM12 12h2v2h-2zM10 14h2v2h-2zM14 14h2v2h-2z"),
    "crate-lid":       ("Crate, as a lidded moving crate with grips notched into its sides.",
                        "M0 2h16v2H0zM1 6h14v10H1zM1 9v2h2V9zM13 9v2h2V9z"),
    "open-first-rule": ("Open first, as the label's own double rule around a label.",
                        "M0 2h16v12H0zM2 4v8h12V4zM3 5h10v6H3zM4 6v4h8V6z"),
}

def svg(d):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor">'
            f'<path fill-rule="evenodd" d="{d}"/></svg>\n')

def symbol(name, d):
    return f'<symbol id="i-{name}" viewBox="0 0 16 16"><path fill-rule="evenodd" d="{d}"/></symbol>'

if __name__ == "__main__":
    out = os.path.join(ROOT, "set"); os.makedirs(out, exist_ok=True)
    for name, (_, d) in {**ICONS, **VARIANTS}.items():
        p = os.path.join(out, f"{name}.svg")
        open(p, "w").write(svg(d))
        print(f"set/{name}.svg".ljust(26), f"{os.path.getsize(p):4d} B")
    # The sprite: inline once, then <svg class="i"><use href="#i-box"/></svg>.
    # fill="currentColor" on the root so every <use> takes the text colour.
    sprite = ('<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" style="display:none" aria-hidden="true">'
              + "".join(symbol(n, d) for n, (_, d) in {**ICONS, **VARIANTS}.items()) + "</svg>\n")
    sp = os.path.join(ROOT, "sprite.svg"); open(sp, "w").write(sprite)
    print("sprite.svg".ljust(26), f"{os.path.getsize(sp):4d} B")
