"""Building a one-line summary from the itemised contents.

Plain assembly, not a model: the items have already been typed or reviewed, so
a summary of them should be instant, predictable and available offline.
"""

from movingbox import summarise


def items(*names):
    return [{"name": n, "qty": 1} for n in names]


def test_a_short_list_becomes_a_comma_list():
    assert summarise.from_items(items("kettle", "toaster")) == "kettle, toaster"


def test_quantities_are_carried_through():
    contents = [{"name": "baking pan", "qty": 3}, {"name": "kettle", "qty": 1}]

    assert summarise.from_items(contents) == "3 baking pans, kettle"


def test_a_plural_is_not_invented_for_a_word_already_plural():
    contents = [{"name": "scissors", "qty": 2}, {"name": "boxes", "qty": 2}]

    assert summarise.from_items(contents) == "2 scissors, 2 boxes"


def test_duplicates_are_merged_rather_than_repeated():
    contents = [{"name": "mug", "qty": 2}, {"name": "Mug", "qty": 3}]

    assert summarise.from_items(contents) == "5 mugs"


def test_an_empty_list_gives_an_empty_summary():
    assert summarise.from_items([]) == ""


def test_the_summary_is_capped_to_fit_a_label():
    contents = items(*[f"thing number {n}" for n in range(200)])

    result = summarise.from_items(contents)

    assert len(result) <= summarise.MAX_LENGTH


def test_truncation_says_how_many_were_left_out():
    # "and 12 more" is far more useful on tape than a sentence cut mid-word.
    contents = items(*[f"item {n}" for n in range(60)])

    result = summarise.from_items(contents)

    assert "more" in result
    assert not result.endswith(",")


def test_items_keep_their_given_order():
    # The order things were listed usually reflects what is most notable.
    assert summarise.from_items(items("piano", "sock")).startswith("piano")


def child(kind="bag", *, summary=None, size=None):
    """A nested record, shaped as store.children_of returns one."""
    return {"kind": kind, "content_summary": summary, "size": size}


class TestWhatIsNestedInside:
    """A container's children are contents too.

    A crate holding three bags is not empty, and its label should not say so.
    """

    def test_unnamed_children_merge_rather_than_repeating(self):
        # Three bags, none of them described: "3 bags", not "bag, bag, bag".
        result = summarise.from_contents([], [child(), child(), child()])

        assert result == "3 bags"

    def test_a_child_that_has_a_summary_contributes_it(self):
        inside = [child("box", summary="kettle and mugs")]

        assert summarise.from_contents([], inside) == "kettle and mugs"

    def test_a_child_without_one_is_named_by_its_size_and_kind(self):
        # The same words the list rows use, so the label and the app agree.
        inside = [child("crate", size="large"), child("crate", size="large")]

        assert summarise.from_contents([], inside) == "2 large crates"

    def test_a_loose_thing_inside_is_named_by_what_it_is(self):
        assert summarise.from_contents([], [child("item")]) == "loose item"

    def test_items_come_before_what_is_nested_inside(self):
        result = summarise.from_contents(items("kettle"), [child()])

        assert result == "kettle, bag"

    def test_a_child_named_like_an_item_merges_with_it(self):
        # Counting is the whole point of the merge: two bags, not "bag, bag".
        result = summarise.from_contents([{"name": "bag", "qty": 1}], [child()])

        assert result == "2 bags"

    def test_with_no_children_it_is_the_plain_assembly(self):
        assert summarise.from_contents(items("kettle", "toaster")) == "kettle, toaster"
