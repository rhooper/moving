"""Marking a record fragile when something breakable goes into it.

Asked for as "given it contains a mug or figurine, auto-add fragile". The
rule is words, not a model: offline, the same answer every time, and here to
be read. The names below are B-0062's own.
"""

import pytest

from movingbox import db, fragility, store


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


class TestWhatIsBreakable:
    @pytest.mark.parametrize(
        "name",
        [
            "black devs mug",
            "axolotl figurine from daniel",
            "ceramic candle holder",
            "Wine glasses",
            "porcelain teacup",
            "small vase",
            "bathroom mirror",
            "spare light bulbs",
            "27in monitor",
            "picture frame",
            "box of christmas ornaments",
            "Mugs",
        ],
    )
    def test_breakable(self, name):
        assert fragility.breakable(name)

    @pytest.mark.parametrize(
        "name",
        [
            "weight plate",
            "wooden star light (rose design)",
            "power supply unit",
            "plastic cups",
            "steel mixing bowl",
            "travel mug (stainless steel)",
            "suction cup hooks",
            "paper plates",
            "drag chain for desk wires",
            "screwdriver",
            "",
        ],
    )
    def test_not_breakable(self, name):
        assert not fragility.breakable(name)

    def test_the_reasons_are_the_breakable_names_in_the_whole_subtree(self):
        nodes = [
            {"id": 1, "parent_id": None, "depth": 0, "items": [{"name": "weight plate"}]},
            {
                "id": 2,
                "parent_id": 1,
                "depth": 1,
                "content_summary": "Kitchen gear - mugs, bowls",
                "items": [{"name": "black devs mug"}, {"name": "remote organizer"}],
            },
        ]

        assert fragility.reasons(nodes) == ["black devs mug"]

    def test_a_nested_record_s_summary_counts_when_it_lists_nothing(self):
        nodes = [
            {"id": 1, "parent_id": None, "depth": 0, "items": []},
            {"id": 2, "parent_id": 1, "depth": 1, "content_summary": "wine glasses", "items": []},
        ]

        assert fragility.reasons(nodes) == ["wine glasses"]


class TestMarking:
    def test_something_breakable_marks_the_record(self, conn):
        code = store.create_box(conn)["code"]

        assert fragility.mark(conn, code, because="black devs mug", actor="auto")

        assert store.get_box(conn, code)["fragile"] == 1

    def test_it_is_logged_with_its_actor_and_why(self, conn):
        code = store.create_box(conn)["code"]

        fragility.mark(conn, code, because="black devs mug", actor="auto")

        logged = conn.execute(
            "SELECT field, from_value, to_value, actor, note FROM events"
            " WHERE box_code = ? AND kind = 'edit'",
            (code,),
        ).fetchall()
        assert [dict(row) for row in logged] == [
            {
                "field": "fragile",
                "from_value": "0",
                "to_value": "1",
                "actor": "auto",
                "note": "holds black devs mug",
            }
        ]

    def test_an_already_fragile_record_is_left_alone(self, conn):
        code = store.create_box(conn, fragile=1)["code"]

        assert not fragility.mark(conn, code, because="vase", actor="auto")

    def test_a_person_s_clearing_wins_for_good(self, conn):
        # They looked at the mug and decided; marking it again on every new
        # item would undo them every time.
        code = store.create_box(conn)["code"]
        fragility.mark(conn, code, because="mug", actor="auto")
        store.update_box(conn, code, fragile=0)

        assert not fragility.mark(conn, code, because="vase", actor="auto")
        assert store.get_box(conn, code)["fragile"] == 0

    def test_the_photo_reader_s_own_clearing_is_not_a_person_s(self, conn):
        code = store.create_box(conn)["code"]
        store.update_box(conn, code, fragile=1, actor="ai")
        store.update_box(conn, code, fragile=0, actor="ai")

        assert fragility.mark(conn, code, because="vase", actor="auto")

    def test_a_binned_record_is_left_alone(self, conn, config):
        code = store.create_box(conn)["code"]
        store.delete_box(conn, config, code)

        assert not fragility.mark(conn, code, because="vase", actor="auto")

    def test_nothing_is_ever_cleared(self, conn):
        assert not hasattr(fragility, "clear")


class TestMarkingForWhatItHolds:
    def test_a_breakable_item_marks_it(self, conn):
        code = store.create_box(conn)["code"]
        store.add_item(conn, code, name="weight plate")
        store.add_item(conn, code, name="axolotl figurine from daniel")

        assert fragility.mark_for_contents(conn, code, actor="auto") == [
            "axolotl figurine from daniel"
        ]
        assert store.get_box(conn, code)["fragile"] == 1

    def test_nothing_breakable_marks_nothing(self, conn):
        code = store.create_box(conn)["code"]
        store.add_item(conn, code, name="weight plate")

        assert fragility.mark_for_contents(conn, code, actor="auto") == []
        assert store.get_box(conn, code)["fragile"] == 0
