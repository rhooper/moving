"""Contact sheet of concept sketches: big, nav size, favicon size, plus a
nearest-neighbour zoom of the true 16 px and 32 px rasters. Scratch tool."""
import base64, glob, os, sys
src = sys.argv[1]; out = sys.argv[2]
files = sorted(glob.glob(os.path.join(src, "*.svg")))
PAPER = {"light": ("#fafaf8", "#000000", "#d8d8d2"), "dark": ("#0a0a0a", "#ffffff", "#2a2a28")}
def inline(svg, px):
    return svg.replace("<svg ", f'<svg width="{px}" height="{px}" ', 1)
def uri(svg, ink):
    s = svg.replace("currentColor", ink)
    return "data:image/svg+xml;base64," + base64.b64encode(s.encode()).decode()
rows = []
for f in files:
    svg = open(f).read().strip()
    name = os.path.basename(f)[:-4]
    cells = []
    for mode, (paper, ink, line) in PAPER.items():
        cells.append(f'''<div class="cell" style="background:{paper};color:{ink};border-color:{line}">
          {inline(svg,128)} {inline(svg,32)} {inline(svg,24)} {inline(svg,16)}
          <canvas data-src="{uri(svg, ink)}" data-px="32" width="32" height="32" style="width:128px;height:128px"></canvas>
          <canvas data-src="{uri(svg, ink)}" data-px="16" width="16" height="16" style="width:128px;height:128px"></canvas>
        </div>''')
    rows.append(f'<section><h2>{name} <small>{len(svg)} B</small></h2><div class="pair">{"".join(cells)}</div></section>')
html = f'''<!doctype html><meta charset="utf-8"><style>
body{{margin:0;padding:16px;font:600 13px/1.3 -apple-system,sans-serif;background:#bbb}}
h2{{margin:10px 0 4px;font-size:13px}} small{{font-weight:400}}
.pair{{display:flex;gap:8px}} .cell{{display:flex;align-items:flex-end;gap:14px;padding:12px;border:1px solid}}
canvas{{image-rendering:pixelated;outline:1px dashed #888}}
</style>{"".join(rows)}
<script>
for (const c of document.querySelectorAll("canvas")) {{
  const im = new Image(); const px = +c.dataset.px;
  im.onload = () => c.getContext("2d").drawImage(im, 0, 0, px, px);
  im.src = c.dataset.src;
}}
</script>'''
open(out, "w").write(html)
print(out, len(files), "sketches")
