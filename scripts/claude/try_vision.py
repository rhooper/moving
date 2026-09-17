"""Run the real vision provider against an image, to check the plumbing.

Not a test -- it calls a live model, so it is slow and non-deterministic. Use
it to confirm Ollama is reachable, the model is pulled, and a reply parses into
a draft.

Usage: uv run python scripts/claude/try_vision.py [image.jpg] [--model M]
"""

import io
import sys
import time

from PIL import Image, ImageDraw

from movingbox.config import from_env
from movingbox.vision import base
from movingbox.vision.ollama import OllamaProvider


def synthetic() -> bytes:
    """A crude stand-in for a photo of an open box.

    Drawn shapes with labels: enough to prove the request/parse path works when
    no real photograph is to hand.
    """
    image = Image.new("RGB", (1024, 768), (205, 180, 145))
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 984, 728], outline=(120, 95, 70), width=14)
    things = [
        ((90, 110, 380, 330), (60, 60, 65), "saucepan"),
        ((420, 110, 700, 300), (200, 195, 185), "mixing bowl"),
        ((740, 110, 940, 360), (150, 40, 35), "kettle"),
        ((90, 380, 460, 560), (90, 105, 130), "baking tray"),
        ((500, 380, 940, 690), (230, 225, 210), "tea towels"),
    ]
    for box, colour, label in things:
        draw.rectangle(box, fill=colour)
        draw.text((box[0] + 10, box[1] + 10), label, fill=(255, 255, 255))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def main() -> int:
    args = sys.argv[1:]
    model = None
    if "--model" in args:
        index = args.index("--model")
        model = args[index + 1]
        del args[index : index + 2]

    config = from_env()
    model = model or config.vision_model

    if args:
        data = open(args[0], "rb").read()
        source = args[0]
    else:
        data = synthetic()
        source = "synthetic drawing"

    print(f"ollama  {config.ollama_url}")
    print(f"model   {model}")
    print(f"image   {source} ({len(data):,} bytes)")
    print()

    provider = OllamaProvider(config.ollama_url)
    started = time.monotonic()
    try:
        draft = provider.draft([data], model=model)
    except base.DraftUnreadable as failure:
        print(f"FAILED after {time.monotonic() - started:.1f}s: {failure}")
        return 1

    elapsed = time.monotonic() - started
    print(f"drafted in {elapsed:.1f}s")
    print(f"  summary    {draft.summary}")
    print(f"  fragile    {draft.fragile}")
    print(f"  confidence {draft.confidence}")
    print("  items:")
    for item in draft.items:
        qty = f" x{item.qty}" if item.qty > 1 else ""
        category = f"  [{item.category}]" if item.category else ""
        print(f"    - {item.name}{qty}{category}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
