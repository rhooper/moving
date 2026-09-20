"""Build icons/proof.html from the set. Scratch tool.
Usage: python3 proof.py && ./shot.sh ../proof.html ../proof.png 1900 H"""
import base64, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icons import ICONS, VARIANTS, ROOT

W = os.path.dirname(os.path.abspath(__file__))
FONT = "file:///path/to/moving/src/movingbox/labels/fonts/Inter.ttf"
HOME = 'M0 0h16v16H0zM2 2v12h12V2zM8 4l4 4v4H4V8z'
THEMES = {"light": ("#fafaf8", "#000000"), "dark": ("#0a0a0a", "#ffffff")}

def ic(d, px, cls=""):
    return (f'<svg class="{cls}" width="{px}" height="{px}" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
            f'<path fill-rule="evenodd" d="{d}"/></svg>')
def uri(d, ink):
    s = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="{ink}"><path fill-rule="evenodd" d="{d}"/></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(s.encode()).decode()
def png(path):
    return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
def sk(stem):
    p = os.path.join(W, "sketch1", stem + ".svg")
    if not os.path.exists(p): p = os.path.join(W, "sketch2", stem + ".svg")
    s = open(p).read(); return s[s.index('d="') + 3 : s.index('"/>')]

def zoom(d, px, theme):
    return (f'<canvas data-src="{uri(d, THEMES[theme][1])}" data-px="{px}" width="{px}" height="{px}" '
            f'style="width:{px * (96 // px)}px;height:{px * (96 // px)}px"></canvas>')

def sizes_row(name, meaning, d, variant=False):
    cells = ""
    for theme in ("light", "dark"):
        cells += (f'<div class="strip {theme}">' + "".join(ic(d, p) for p in (64, 32, 24, 16))
                  + f'<span class="gap"></span>{zoom(d, 16, theme)}{zoom(d, 24, theme)}</div>')
    size = os.path.getsize(os.path.join(ROOT, "set", f"{name}.svg"))
    tag = ' <em>variant</em>' if variant else ""
    return f'<div class="row"><div class="who"><b>{name}</b>{tag}<span>{size} B</span><p>{meaning}</p></div>{cells}</div>'

def group(title, names, variants=()):
    rows = "".join(sizes_row(n, *ICONS[n]) for n in names)
    rows += "".join(sizes_row(n, *VARIANTS[n], variant=True) for n in variants)
    return f'<section><h2>{title}</h2>{rows}</section>'

# ---------- context mocks (the app's own rules, copied from index.html) ----------
NAV = [("items", "Items"), ("scan", "Scan"), ("new", "New"), ("settings", "Settings")]
KINDS = [("box", "Box"), ("tub", "Tub"), ("crate", "Crate"), ("bag", "Bag"), ("item", "Loose item"), ("furniture", "Furniture")]
FLAGS = [("fragile", "Fragile"), ("heavy", "Heavy"), ("open-first", "Open first")]

def bar(theme, style, px=24, current="items"):
    items = ""
    for n, word in NAV:
        cur = ' aria-current="page"' if n == current else ""
        items += f'<a{cur}>{ic(ICONS[n][1], px)}<span>{word}</span></a>'
    return f'<nav class="bar {style}">{items}</nav>'

def phone(theme, width, style, px):
    return f'''<div class="mock phone {theme}" style="width:{width}px">
  <a class="home">{ic(HOME, 32)}</a>
  <div class="main">
    <div class="srow"><input placeholder="Find an item, or something inside one"><button class="btn">Search</button></div>
    <div class="section"><h2>37 items</h2>{rows(3)}</div>
  </div>
  {bar(theme, style, px)}
</div>'''

def desktop(theme, px=20):
    items = "".join(
        f'<a{" aria-current=page" if n == "items" else ""}>{ic(ICONS[n][1], px)}<span>{w}</span></a>' for n, w in NAV)
    return f'''<div class="mock desk {theme}">
  <a class="home dk">{ic(HOME, 24)}<b>MOVING</b></a>
  <nav class="bar inline">{items}</nav>
  <div class="main"><div class="srow"><input placeholder="Find an item, or something inside one"><button class="btn">Search</button></div></div>
</div>'''

ROWS = [("box", "B-0042", "pots, 3 baking pans, kettle, 2 cutting boards", "large box", "packed", True),
        ("tub", "T-0007", "winter coats, scarves", "tub", "open", False),
        ("crate", "C-0003", "records A–K", "XL crate", "loaded", False),
        ("bag", "G-0011", "bedding, two pillows", "bag", "packed", False),
        ("item", "I-0007", "Bicycle (Trek hybrid)", "loose item", "delivered", False),
        ("furniture", "F-0002", "Oak sideboard", "furniture", "open", False)]
def rows(n=None, icons=True):
    out = ""
    for kind, code, s, k, st, photo in ROWS[:n]:
        t = '<span class="t photo"></span>' if photo else (f'<span class="t">{ic(ICONS[kind][1], 26)}</span>' if icons else '<span class="t old"></span>')
        out += f'<li><a>{t}<span class="c">{code}</span><span class="s">{s}</span><span class="w"><span class="k">{k}</span><span>{st}</span></span></a></li>'
    return f'<ul class="boxlist">{out}</ul>'

def seg(theme, mode, width=320):
    btns = ""
    for i, (n, word) in enumerate(KINDS):
        chk = ' class="on"' if i == 0 else ""
        inner = {"words": word, "both": ic(ICONS[n][1], 20) + word, "icons": ic(ICONS[n][1], 24)}[mode]
        btns += f'<label><span{chk}>{inner}</span></label>'
    cap = {"words": "words only (today)", "both": "icon and word", "icons": "icons only"}[mode]
    return f'<div class="mock segmock {theme}" style="width:{width}px"><p class="dlabel">Kind</p><div class="seg-row">{btns}</div><p class="cap">{cap}</p></div>'

def chips(theme):
    off = "".join(f'<button class="chip">{ic(ICONS[n][1], 20)}{w}</button>' for n, w in FLAGS)
    on = "".join(f'<button class="chip on">{ic(ICONS[n][1], 20)}{w}</button>' for n, w in FLAGS)
    return f'<div class="mock chipmock {theme}"><p class="dlabel">Handling</p><div class="flags-set">{off}</div><div class="flags-set">{on}</div><p class="meta">These print on the label.</p></div>'

def label_chips():
    mine = "".join(f'<span class="lchip">{ic(ICONS[n][1], 46)}{w.upper()}</span>' for n, w in FLAGS[:2])
    mine += f'<span class="lchip">{ic(ICONS["open-first"][1], 46)}OPEN FIRST</span>'
    mine += f'<span class="lchip">{ic(VARIANTS["open-first-rule"][1], 46)}OPEN FIRST</span>'
    return f'''<div class="compare">
  <figure><img src="{png(os.path.join(W, "label_chips_row.png"))}" style="height:100px"><figcaption>the label, as printed (layout.py)</figcaption></figure>
  <figure><div class="lchips">{mine}</div><figcaption>this set, in the same chip &mdash; the last two are the open-first primary and its variant</figcaption></figure>
  <figure><img src="{png(os.path.join(W, "label_openfirst_small.png"))}" style="height:100px"><figcaption>open first on the label: a double rule round the whole thing, not a chip</figcaption></figure>
</div>'''

def track(theme):
    steps = [("s1-open", "open", "done"), ("s2-packed", "packed", "done"), ("s3-loaded", "loaded", "now"), ("s4-delivered", "delivered", ""), ("s5-unpacked", "unpacked", "")]
    cells = "".join(f'<button class="step {c}">{ic(sk(f), 20)}<span>{w}</span></button>' for f, w, c in steps)
    return f'<div class="mock trackmock {theme}" style="width:400px"><h2>Where it is now</h2><div class="track">{cells}</div></div>'

KILLED = [
    ("n4b-settings-gear", "Gear: round, generic, mush at 16."),
    ("n1b-items-labels", "Items as stacked bands: the hamburger menu."),
    ("k3b-crate-x", "Crate with a cross brace: reads as close/cancel."),
    ("k1d-box-3flap", "Box with three flaps: a crown."),
    ("k1b-box-closed", "Closed box with a label patch: could be anything."),
    ("k5b-tag-hang", "Hanging tag: a padlock."),
    ("k6b-armchair", "Armchair, front on: a mug."),
    ("k6c-chest", "Chest of drawers: clear, but a server rack too."),
    ("f2b-heavy-knob", "Weight with a knob: a lampshade; the loop is the label's."),
    ("f3c-openfirst-flaps", "Open box with a stick: a crown again."),
    ("s2-packed", "Packed, as a taped box: a pause button."),
    ("s3-loaded", "Loaded, as a truck: a blob at 16."),
    ("s4-delivered", "Delivered, as a house: it is the home mark."),
    ("s5-unpacked", "Unpacked: the open box again, with a hole. Not distinct from open."),
    ("x1-put-in", "Put it inside: reads as download."),
    ("x2-take-out", "Take it out: reads as upload."),
    ("x3-contains", "Contains: it is a finder pattern."),
]
def killed():
    figs = "".join(f'<figure>{ic(sk(f), 48)}<div class="sm">{ic(sk(f), 24)}{ic(sk(f), 16)}</div><figcaption>{why}</figcaption></figure>' for f, why in KILLED)
    return f'<section class="light killed"><h2>Drawn, rendered, killed</h2><div class="kills">{figs}</div></section>'

family = "".join(
    f'<div class="strip fam {t}">{ic(HOME, 32, "home")}<i></i>' + "".join(ic(d, 32) for _, d in ICONS.values()) + '</div>'
    for t in ("light", "dark"))

html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Icons &mdash; proof sheet</title>
<style>
@font-face {{ font-family: "Inter"; src: url("{FONT}") format("truetype-variations"); font-weight: 100 900; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 26px 30px 40px; width: 1900px; background: #c9c9c4; color: #000; font: 400 14px/1.4 Inter, system-ui, sans-serif; }}
h1 {{ font-size: 32px; font-weight: 800; letter-spacing: -0.04em; margin: 0 0 2px; }}
body > p {{ margin: 0 0 18px; max-width: 120ch; }}
section {{ margin-bottom: 26px; }}
section > h2 {{ margin: 0; padding: 7px 14px; background: #000; color: #fafaf8; font-size: 17px; font-weight: 800; letter-spacing: .02em; text-transform: uppercase; }}
section > h2 small {{ font-weight: 400; text-transform: none; letter-spacing: 0; opacity: .8; margin-left: 12px; }}
.light {{ --paper: #fafaf8; --ink: #000; --muted: #6b6b66; --line: #d8d8d2; --sunk: #efefe9; --signal: #ff5a1f; }}
.dark  {{ --paper: #0a0a0a; --ink: #fff; --muted: #8f8f88; --line: #2a2a28; --sunk: #161614; --signal: #ff5a1f; }}
.strip, .mock, .who {{ background: var(--paper); color: var(--ink); }}
.row {{ display: flex; border-top: 1px solid #c9c9c4; }}
.who {{ flex: 0 0 300px; padding: 10px 14px; background: #fff; color: #000; }}
.who b {{ font-size: 15px; }} .who em {{ font-style: normal; font-size: 11px; background: #000; color: #fff; padding: 1px 5px; margin-left: 6px; }}
.who span {{ float: right; font-size: 12px; color: #666; font-variant-numeric: tabular-nums; }}
.who p {{ margin: 3px 0 0; font-size: 12.5px; color: #444; }}
.strip {{ flex: 1; display: flex; align-items: flex-end; gap: 18px; padding: 12px 18px; }}
.strip .gap {{ flex: 1; }}
.strip.fam {{ align-items: center; gap: 22px; padding: 18px 22px; }} .strip.fam i {{ width: 1px; height: 32px; background: var(--line); }}
.family {{ display: flex; }}
canvas {{ image-rendering: pixelated; outline: 1px dashed rgb(128 128 128 / .6); }}
svg {{ display: block; flex: none; }}
.ctx {{ display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; padding: 14px 0 0; }}
.cap {{ font-size: 11px; color: var(--muted); margin: 8px 0 0; }}
/* --- the app --- */
.mock {{ position: relative; overflow: hidden; font: 400 17px/1.5 Inter, sans-serif; }}
.mock a {{ color: inherit; text-decoration: none; }}
.phone {{ height: 430px; }}
.desk {{ width: 1058px; height: 150px; }}
.mock .main {{ max-width: 34rem; margin: 0 auto; padding: 0 1rem; }}
.home {{ display: flex; align-items: center; gap: .6rem; min-height: 48px; width: max-content; margin: .25rem 0 0 .5rem; padding: 0 .5rem; }}
.home.dk {{ position: absolute; top: 0; left: .5rem; margin: 0; z-index: 2; background: var(--paper); }} .home b {{ font-weight: 800; letter-spacing: -.02em; }}
.srow {{ display: flex; gap: .5rem; }} .srow > :first-child {{ flex: 1; }}
input, button {{ font: inherit; color: var(--ink); background: var(--paper); border: 1px solid var(--line); border-radius: 0; padding: .65rem .75rem; min-height: 48px; }}
.btn {{ background: var(--ink); color: var(--paper); border-color: var(--ink); font-weight: 650; padding-inline: 1rem; }}
.section {{ border-top: 1px solid var(--line); padding-top: 1rem; margin-top: 1.25rem; }}
.section h2, .trackmock h2 {{ font-size: 1rem; font-weight: 650; margin: 0 0 .75rem; }}
.boxlist {{ list-style: none; margin: 0; padding: 0; }}
.boxlist li {{ border-bottom: 1px solid var(--line); }}
.boxlist li > a {{ display: flex; align-items: baseline; gap: .75rem; padding: .6rem 0; }}
.boxlist .t {{ flex: 0 0 44px; width: 44px; height: 44px; align-self: center; background: var(--sunk); border: 1px solid var(--line); display: flex; align-items: center; justify-content: center; color: var(--muted); position: relative; }}
.boxlist .t svg {{ opacity: .45; }}
.boxlist .t.photo {{ background: linear-gradient(135deg, #8a8578, #4a473f); }}
.boxlist .t.old::before {{ content: ""; position: absolute; inset: 0; background: var(--muted); opacity: .45;
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.6' stroke-linejoin='round' stroke-linecap='round'%3E%3Cpath d='M5 11h14v8H5z'/%3E%3Cpath d='M5 11 2.2 6.8'/%3E%3Cpath d='M19 11 21.8 6.8'/%3E%3Cpath d='M5 11 7.6 7.2'/%3E%3Cpath d='M19 11 16.4 7.2'/%3E%3C/svg%3E") center / 26px 26px no-repeat;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.6' stroke-linejoin='round' stroke-linecap='round'%3E%3Cpath d='M5 11h14v8H5z'/%3E%3Cpath d='M5 11 2.2 6.8'/%3E%3Cpath d='M19 11 21.8 6.8'/%3E%3Cpath d='M5 11 7.6 7.2'/%3E%3Cpath d='M19 11 16.4 7.2'/%3E%3C/svg%3E") center / 26px 26px no-repeat; }}
.boxlist .c {{ font-weight: 800; letter-spacing: -.02em; }}
.boxlist .s {{ flex: 1; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.boxlist .w {{ display: flex; flex-direction: column; align-items: flex-end; font-size: .8125rem; line-height: 1.25; color: var(--muted); white-space: nowrap; }}
.boxlist .w .k {{ color: var(--ink); font-weight: 650; }}
.bar {{ position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 2px; background: var(--line); border-top: 1px solid var(--line); }}
.bar a {{ flex: 1; min-height: 48px; display: flex; align-items: center; justify-content: center; gap: 6px; background: var(--paper); font-weight: 650; font-size: .9375rem; }}
.bar.side a {{ gap: 6px; font-size: .875rem; }}
.bar.stack a {{ flex-direction: column; gap: 3px; padding: 6px 0 5px; font-size: .75rem; line-height: 1; }}
.bar a[aria-current="page"] {{ background: var(--ink); color: var(--paper); }}
.bar.inline {{ top: 0; bottom: auto; border-top: 0; border-bottom: 1px solid var(--line); justify-content: center; gap: 1px; }}
.bar.inline a {{ flex: 0 0 auto; padding-inline: 1.25rem; gap: 8px; }}
.desk .main {{ padding-top: 4.5rem; }}
/* pushbuttons */
.segmock, .chipmock, .trackmock {{ padding: 12px 16px 10px; }}
.dlabel {{ display: block; font-size: .8125rem; color: var(--muted); margin: 0 0 .25rem; }}
.seg-row {{ display: flex; flex-wrap: wrap; padding: 0 1px 1px 0; }}
.seg-row::after {{ content: ""; flex: 1000 0 0; }}
.seg-row label {{ display: flex; flex: 1 1 auto; margin: 0 -1px -1px 0; position: relative; }}
.seg-row span {{ flex: 1; display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 48px; padding: .35rem .9rem; border: 1px solid var(--line); color: var(--ink); font-size: .9375rem; font-weight: 650; white-space: nowrap; }}
.seg-row span.on {{ background: var(--ink); border-color: var(--ink); color: var(--paper); z-index: 1; }}
/* chips */
.flags-set {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: .5rem; }}
.chip {{ display: inline-flex; align-items: center; gap: 8px; width: auto; min-height: 48px; padding-inline: 1rem; border: 1px solid var(--line); background: transparent; color: var(--muted); font-weight: 700; }}
.chip.on {{ background: var(--signal); border-color: var(--signal); color: #fff; }}
.meta {{ color: var(--muted); font-size: .9375rem; margin: 0; }}
.compare {{ display: flex; flex-wrap: wrap; gap: 26px; align-items: flex-start; background: #fff; padding: 16px; }}
.compare figure {{ margin: 0; }} .compare figcaption, .kills figcaption {{ font-size: 12px; color: #444; margin-top: 8px; max-width: 46ch; }}
.compare img {{ display: block; }}
.lchips {{ display: flex; gap: 10px; }}
.lchip {{ display: inline-flex; align-items: center; gap: 10px; height: 100px; padding: 0 22px 0 18px; background: #000; color: #fff; font-weight: 800; font-size: 46px; letter-spacing: -.02em; white-space: nowrap; }}
/* status track */
.track {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; }}
.step {{ min-height: 48px; border: 1px solid var(--line); color: var(--muted); font-size: .75rem; font-weight: 650; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; padding: .3rem .1rem; }}
.step.done {{ border-color: var(--ink); color: var(--ink); }}
.step.now {{ background: var(--ink); border-color: var(--ink); color: var(--paper); }}
/* killed */
.kills {{ display: grid; grid-template-columns: repeat(9, 1fr); gap: 18px 20px; padding: 18px 20px; background: var(--paper); color: var(--ink); }}
.kills figure {{ margin: 0; }} .kills .sm {{ display: flex; gap: 8px; align-items: flex-end; margin-top: 6px; }}
</style></head><body>
<h1>Icons &mdash; one set, sixteen marks</h1>
<p>Everything is one evenodd path on the home mark&rsquo;s 16-unit grid: 2-unit strokes, solid masses, holes only where they mean something (a handle, a slot, a crack). Shown at 64, 32, 24 and 16&nbsp;px on paper and on ink from the same <code>currentColor</code> file; the dashed squares are the real 16 and 24&nbsp;px rasters blown up without smoothing. Then each group where it would live, drawn with the app&rsquo;s own rules and Inter.</p>

<section><h2>The family <small>the home mark, then the set, at 32 px</small></h2><div class="family">{family}</div></section>
{group("1 &nbsp;Nav", ["items", "scan", "new", "settings"], ["scan-qr"])}
<section><h2>Nav, in the bar <small>stacked at 24 px so the bar grows from 48 to 56 px; inline at 16 px keeps 48 but is tight at 320; desktop inline at 20 px</small></h2><div class="ctx">
{phone("light", 400, "stack", 24)}{phone("dark", 400, "stack", 24)}{phone("light", 320, "stack", 24)}{phone("dark", 320, "side", 16)}
<div style="display:flex;flex-direction:column;gap:14px">{desktop("light")}{desktop("dark")}</div>
</div></section>

{group("2 &nbsp;Kinds", ["box", "tub", "crate", "bag", "item", "furniture"], ["crate-lid"])}
<section><h2>Kinds, in the pushbutton row at 320 px, and as the list row&rsquo;s empty-thumbnail <small>the row wraps as the real one does</small></h2><div class="ctx">
<div style="display:flex;flex-direction:column;gap:14px">{seg("light", "words")}{seg("light", "both")}{seg("light", "icons")}</div>
<div style="display:flex;flex-direction:column;gap:14px">{seg("dark", "words")}{seg("dark", "both")}{seg("dark", "icons")}</div>
<div class="mock light" style="width:400px;padding:12px 16px"><p class="dlabel">List rows: the kind in the frame a photo would fill (today every row shows the same open box)</p>{rows()}<p class="cap">B-0042 has a cover photo; the rest show their kind at 45% like today&rsquo;s placeholder.</p></div>
<div class="mock dark" style="width:400px;padding:12px 16px"><p class="dlabel">Same, on ink</p>{rows()}<p class="cap">Today, for comparison:</p>{rows(2, icons=False)}</div>
</div></section>

{group("3 &nbsp;Handling flags", ["fragile", "heavy", "open-first"], ["open-first-rule"])}
<section><h2>Flags, beside the label&rsquo;s own, and in the app&rsquo;s chips</h2>
{label_chips()}
<div class="ctx">{chips("light")}{chips("dark")}</div></section>

<section><h2>4 &nbsp;Status track &mdash; tried, and it does not work <small>five marks in the real track at 400 px</small></h2><div class="ctx">{track("light")}{track("dark")}
<p style="max-width:60ch;margin:0">Open and unpacked are the same open box; delivered is the home mark; loaded is a blob at the track&rsquo;s size. The track already draws progress &mdash; done, now, next &mdash; and the words are 12 px tall in a cell 70 px wide. Icons here would add a second thing to read without adding meaning.</p></div></section>

{killed()}
<script>
for (const c of document.querySelectorAll("canvas")) {{ const im = new Image(), px = +c.dataset.px; im.onload = () => c.getContext("2d").drawImage(im, 0, 0, px, px); im.src = c.dataset.src; }}
</script></body></html>'''
out = os.path.join(ROOT, "proof.html"); open(out, "w").write(html); print(out, len(html))
