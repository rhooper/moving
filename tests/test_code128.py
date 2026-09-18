"""Code 128 under the box number, for a keyboard-wedge barcode reader.

The encoder is hand-written -- a 107-row pattern table and a checksum -- so the
tests do not trust it: zbar has to read back every symbol value in the table,
including the ones that only ever appear as a checksum.
"""

import itertools

import pytest
from PIL import Image, ImageDraw

from movingbox.labels import code128, layout

pyzbar = pytest.importorskip("pyzbar.pyzbar", reason="needs `brew install zbar`")


def read(image) -> list[str]:
    found = pyzbar.decode(image, symbols=[pyzbar.ZBarSymbol.CODE128])
    return [f.data.decode() for f in found]


def drawn(text: str, module: int = 3, height: int = 60) -> Image.Image:
    width = code128.width(text, module) + 20 * module
    image = Image.new("L", (width, height + 40), 255)
    code128.draw(ImageDraw.Draw(image), 10 * module, 20, text, module=module, height=height)
    return image


class TestTheEncoding:
    def test_a_symbol_is_eleven_modules_and_the_stop_is_thirteen(self):
        # start + 6 data + checksum, then the 13-module stop.
        assert len(code128.modules("B-0042")) == 11 * 8 + 13

    def test_it_begins_and_ends_with_a_bar(self):
        modules = code128.modules("B-0042")

        assert modules[0] and modules[-1]

    def test_every_pattern_is_eleven_modules_wide(self):
        for value, pattern in enumerate(code128.PATTERNS[:106]):
            assert sum(int(w) for w in pattern) == 11, f"pattern {value}"
        assert sum(int(w) for w in code128.PATTERNS[106]) == 13

    def test_no_two_patterns_are_the_same(self):
        assert len(set(code128.PATTERNS)) == len(code128.PATTERNS) == 107

    def test_text_outside_the_printable_set_is_refused(self):
        with pytest.raises(ValueError):
            code128.modules("café")


class TestAgainstARealDecoder:
    @pytest.mark.parametrize("code", ["B-0042", "D001", "Z06-001", "CAM-001", "I-0007"])
    def test_the_codes_in_use_read_back(self, code):
        assert read(drawn(code)) == [code]

    def test_every_data_symbol_reads_back(self):
        # Values 0-94 are the printable characters of code set B.
        for value in range(95):
            text = f"A{chr(value + 32)}Z"
            assert read(drawn(text)) == [text], f"symbol value {value} ({text!r})"

    def test_every_checksum_only_symbol_reads_back(self):
        # 95-102 never appear as data in set B, but any of them can turn up as
        # the checksum -- so find a text for each and make zbar check it.
        wanted = set(range(95, 103))
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        for letters in itertools.product(alphabet, repeat=3):
            text = "".join(letters)
            check = code128.checksum(text)
            if check in wanted:
                assert read(drawn(text)) == [text], f"checksum value {check} ({text!r})"
                wanted.discard(check)
            if not wanted:
                break
        assert not wanted, f"never exercised checksum values {sorted(wanted)}"


class TestOnTheLabel:
    def label(self, **overrides):
        fields = {
            "code": "B-0042",
            "url": "https://test.example.ts.net/b/B-0042",
            "room": "Kitchen",
            "summary": "pots, pans, kettle",
            "flags": (),
        }
        fields.update(overrides)
        return layout.LabelData(**fields)

    @pytest.mark.parametrize("code", ["B-0042", "D001", "Z06-001", "CAM-001"])
    def test_the_barcode_is_the_box_number_and_nothing_else(self, code):
        image = layout.render(self.label(code=code), orientation="landscape")

        assert read(image) == [code]

    def test_the_open_first_border_does_not_crowd_it(self):
        # The double rule runs down the left edge; the barcode keeps a full
        # quiet zone clear of it, or a reader sees the rule as another bar.
        image = layout.render(self.label(flags=("OPEN FIRST",)), orientation="landscape")

        assert read(image) == ["B-0042"]

    def test_the_qr_still_reads_beside_it(self):
        image = layout.render(self.label(), orientation="landscape")
        found = pyzbar.decode(image, symbols=[pyzbar.ZBarSymbol.QRCODE])

        assert [f.data.decode() for f in found] == [self.label().url]

    def test_it_moves_nothing(self):
        # Thin, in the gap under the number: the room band stays where it was.
        image = layout.render(self.label(), orientation="landscape").convert("L")
        row = image.crop((0, layout.BAND_TOP, image.width, layout.BAND_TOP + 1))
        above = image.crop((0, layout.BAND_TOP - 1, image.width - 300, layout.BAND_TOP))

        assert row.getextrema() == (0, 0)
        assert above.getextrema() == (255, 255)

    def test_the_gap_around_the_room_band_is_ten(self):
        assert layout.BAND_GAP == 10
