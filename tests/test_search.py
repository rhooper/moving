"""Full-text search over boxes, their items, photos and rooms."""

from movingbox import search


def make_box(conn, code="B-0001", **cols):
    cols.setdefault("content_summary", None)
    keys = ["code", *cols]
    values = [code, *cols.values()]
    placeholders = ", ".join("?" * len(keys))
    conn.execute(f"INSERT INTO boxes ({', '.join(keys)}) VALUES ({placeholders})", values)
    return conn.execute("SELECT id FROM boxes WHERE code = ?", (code,)).fetchone()[0]


def codes(conn, query):
    return [
        conn.execute("SELECT code FROM boxes WHERE id = ?", (box_id,)).fetchone()[0]
        for box_id in search.search(conn, query)
    ]


def test_box_is_findable_by_its_content_summary(conn):
    box_id = make_box(conn, content_summary="pots, baking pans, stand mixer")
    search.reindex_box(conn, box_id)

    assert codes(conn, "mixer") == ["B-0001"]


def test_box_is_findable_by_an_item_name(conn):
    box_id = make_box(conn)
    conn.execute("INSERT INTO items (box_id, name) VALUES (?, 'cafetiere')", (box_id,))
    search.reindex_box(conn, box_id)

    assert codes(conn, "cafetiere") == ["B-0001"]


def test_box_is_findable_by_its_destination_room(conn):
    conn.execute("INSERT INTO rooms (name) VALUES ('Main Bedroom')")
    room_id = conn.execute("SELECT id FROM rooms").fetchone()[0]
    box_id = make_box(conn, destination_room_id=room_id)
    search.reindex_box(conn, box_id)

    assert codes(conn, "bedroom") == ["B-0001"]


def test_box_is_findable_by_a_photo_caption(conn):
    box_id = make_box(conn)
    conn.execute(
        "INSERT INTO photos (box_id, filename, sha256, caption) VALUES (?, 'a.jpg', 'x', ?)",
        (box_id, "open box showing camping stove"),
    )
    search.reindex_box(conn, box_id)

    assert codes(conn, "camping stove") == ["B-0001"]


def test_box_is_findable_by_its_hyphenated_code(conn):
    # A hyphen is an operator in FTS5 syntax; unescaped, 'B-0001' would error
    # or silently match nothing -- and scanning a label searches exactly this.
    box_id = make_box(conn, code="B-0042")
    search.reindex_box(conn, box_id)

    assert codes(conn, "B-0042") == ["B-0042"]


def test_search_accepts_punctuation_without_raising(conn):
    box_id = make_box(conn, content_summary="pots and pans")
    search.reindex_box(conn, box_id)

    # Users type whatever they like into a search box; none of this may crash.
    for query in ["pots & pans", '"unbalanced', "NEAR(", "*", "a OR", "", "   "]:
        search.search(conn, query)


def test_prefix_of_a_word_matches(conn):
    box_id = make_box(conn, content_summary="espresso machine")
    search.reindex_box(conn, box_id)

    assert codes(conn, "espres") == ["B-0001"]


def test_reindexing_drops_contents_that_no_longer_exist(conn):
    box_id = make_box(conn)
    conn.execute("INSERT INTO items (box_id, name) VALUES (?, 'kettle')", (box_id,))
    search.reindex_box(conn, box_id)
    assert codes(conn, "kettle") == ["B-0001"]

    conn.execute("DELETE FROM items WHERE box_id = ?", (box_id,))
    search.reindex_box(conn, box_id)

    assert codes(conn, "kettle") == []


def test_reindexing_a_box_twice_does_not_duplicate_it(conn):
    box_id = make_box(conn, content_summary="lamp")
    search.reindex_box(conn, box_id)
    search.reindex_box(conn, box_id)

    assert codes(conn, "lamp") == ["B-0001"]


def test_deleted_box_is_removed_from_the_index(conn):
    box_id = make_box(conn, content_summary="lamp")
    search.reindex_box(conn, box_id)

    conn.execute("DELETE FROM boxes WHERE id = ?", (box_id,))
    search.reindex_box(conn, box_id)

    assert codes(conn, "lamp") == []


def test_reindex_all_rebuilds_the_whole_index(conn):
    first = make_box(conn, code="B-0001", content_summary="toaster")
    second = make_box(conn, code="B-0002", content_summary="blender")
    assert codes(conn, "toaster") == []  # nothing indexed yet

    indexed = search.reindex_all(conn)

    assert indexed == 2
    assert codes(conn, "toaster") == ["B-0001"]
    assert codes(conn, "blender") == ["B-0002"]
    assert first != second
