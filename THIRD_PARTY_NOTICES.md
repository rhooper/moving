# Third-party notices

This project's own code is under the MIT license (`LICENSE`). Two things are
bundled in the repository under their own licenses:

| File | What | License |
|---|---|---|
| `src/movingbox/labels/fonts/Inter.ttf` | [Inter](https://rsms.me/inter/), the label and UI typeface | SIL Open Font License 1.1 (`src/movingbox/labels/fonts/OFL.txt`) |
| `web/jsQR.js` | [jsQR](https://github.com/cozmo/jsQR), the in-browser QR reader used where `BarcodeDetector` is missing | Apache License 2.0 |

Python dependencies are installed from PyPI, not bundled; `uv.lock` pins
them. One of them has a copyleft license:

- **`brother_ql`** (printing to Brother QL printers over USB) is GPLv3 or
  later. The MIT license is GPL-compatible, so this source can be distributed
  under MIT, but a distribution that *bundles* `brother_ql` with this code (a
  wheel with vendored dependencies, an app bundle, a container image) is a
  combined work that must meet the GPL's terms.
