"""Box code format: prefix, separator and digit width.

The format lives in the settings table rather than the environment, because
codes belong to the data. A database restored onto another machine has to keep
issuing codes that match the labels already stuck to boxes.
"""

import pytest

from movingbox import codes, db, store


def test_the_default_is_the_original_four_digit_form(conn):
    assert db.next_box_code(conn) == "B-0001"


class TestFormats:
    @pytest.mark.parametrize(
        "prefix,separator,digits,expected",
        [
            ("CAM", "-", 3, "CAM-001"),
            ("D", "", 3, "D001"),
            ("Z06", "-", 3, "Z06-001"),
            ("B", "-", 4, "B-0001"),
            ("BOX", "_", 2, "BOX_01"),
        ],
    )
    def test_each_shape_renders(self, conn, prefix, separator, digits, expected):
        codes.set_format(conn, prefix=prefix, separator=separator, digits=digits)

        assert db.next_box_code(conn) == expected


def test_the_sequence_advances_within_a_prefix(conn):
    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    assert [db.next_box_code(conn) for _ in range(3)] == ["CAM-001", "CAM-002", "CAM-003"]


def test_changing_the_prefix_starts_that_prefix_at_one(conn):
    # Switching to a new prefix should give CAM-001, not CAM-0005 continuing
    # some global count. Codes stay unique because the prefix differs.
    codes.set_format(conn, prefix="B", separator="-", digits=4)
    db.next_box_code(conn)
    db.next_box_code(conn)

    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    assert db.next_box_code(conn) == "CAM-001"


def test_returning_to_a_previous_prefix_does_not_reuse_its_codes(conn):
    # A reused code would point an already-printed label at a different box.
    codes.set_format(conn, prefix="B", separator="-", digits=4)
    first = db.next_box_code(conn)

    codes.set_format(conn, prefix="CAM", separator="-", digits=3)
    db.next_box_code(conn)
    codes.set_format(conn, prefix="B", separator="-", digits=4)

    assert db.next_box_code(conn) != first


def test_a_number_beyond_the_digit_width_still_renders(conn):
    # Padding is a minimum, not a ceiling: box 1000 of a 3-digit format must
    # not silently become 000.
    codes.set_format(conn, prefix="D", separator="", digits=3)
    codes.set_sequence(conn, 1000)

    assert db.next_box_code(conn) == "D1000"


def test_the_next_number_can_be_set(conn):
    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    codes.set_sequence(conn, 42)

    assert db.next_box_code(conn) == "CAM-042"


def test_the_format_survives_reopening_the_database(tmp_path):
    path = tmp_path / "persist.db"
    first = db.connect(path)
    codes.set_format(first, prefix="Z06", separator="-", digits=3)
    first.close()

    second = db.connect(path)
    assert db.next_box_code(second) == "Z06-001"
    second.close()


def test_the_current_format_can_be_read_back(conn):
    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    assert codes.get_format(conn) == {"prefix": "CAM", "separator": "-", "digits": 3}


def test_an_empty_prefix_is_rejected(conn):
    # A bare number is not a scannable identity.
    with pytest.raises(ValueError):
        codes.set_format(conn, prefix="", separator="-", digits=3)


def test_a_prefix_with_whitespace_is_rejected(conn):
    with pytest.raises(ValueError):
        codes.set_format(conn, prefix="CA M", separator="-", digits=3)


@pytest.mark.parametrize("digits", [0, -1, 13])
def test_an_unusable_digit_width_is_rejected(conn, digits):
    with pytest.raises(ValueError):
        codes.set_format(conn, prefix="CAM", separator="-", digits=digits)


def test_a_separator_that_breaks_urls_is_rejected(conn):
    # The code goes in a URL path and on a label; a space or slash would split
    # the path segment the scanner reads back.
    for separator in [" ", "/", "?", "#"]:
        with pytest.raises(ValueError):
            codes.set_format(conn, prefix="CAM", separator=separator, digits=3)


def test_boxes_created_after_a_change_use_the_new_format(conn):
    old = store.create_box(conn)
    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    new = store.create_box(conn)

    assert old["code"] == "B-0001"
    assert new["code"] == "CAM-001"


def test_existing_boxes_keep_the_code_on_their_printed_labels(conn):
    box = store.create_box(conn)
    codes.set_format(conn, prefix="CAM", separator="-", digits=3)

    assert store.get_box(conn, box["code"])["code"] == "B-0001"
