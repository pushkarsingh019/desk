"""Seam 2 — the layout model.

Desk state transitions are pure functions over a plain state object. No DOM,
no server, no I/O. Every transition returns a new state and leaves the one it
was given untouched.
"""

import json

import pytest

from desk import layout


def desk_with(*sheet_ids):
    state = layout.empty_state()
    for sid in sheet_ids:
        state = layout.add_sheet(state, sid)
    return state


def placed(*sheet_ids, at=(0, 0)):
    state = desk_with(*sheet_ids)
    x, y = at
    for i, sid in enumerate(sheet_ids):
        state = layout.place(state, sid, x + i * 400, y)
    return state


# --- the state object is plain and transitions are pure -------------------


def test_empty_state_is_plain_json(desk_json=None):
    import json

    json.dumps(layout.empty_state())


def test_a_transition_does_not_mutate_the_state_it_was_given():
    before = placed("a")
    snapshot = layout.sheet(before, "a")

    layout.move(before, "a", 999, 999)

    assert layout.sheet(before, "a") == snapshot


def test_an_unknown_sheet_is_a_loud_error():
    with pytest.raises(layout.LayoutError):
        layout.move(layout.empty_state(), "ghost", 10, 10)


# --- inbox membership -----------------------------------------------------


def test_a_new_sheet_lands_in_the_inbox():
    state = desk_with("a")

    assert layout.sheet(state, "a")["inbox"] is True
    assert layout.inbox(state) == ["a"]


def test_placing_a_sheet_takes_it_out_of_the_inbox_at_the_drop_position():
    state = layout.place(desk_with("a"), "a", 120, 340)

    sheet = layout.sheet(state, "a")
    assert sheet["inbox"] is False
    assert (sheet["x"], sheet["y"]) == (120, 340)
    assert layout.inbox(state) == []


def test_a_placed_sheet_is_never_returned_to_the_inbox_by_adding_it_again():
    state = layout.place(desk_with("a"), "a", 120, 340)

    state = layout.add_sheet(state, "a")

    sheet = layout.sheet(state, "a")
    assert sheet["inbox"] is False
    assert (sheet["x"], sheet["y"]) == (120, 340)


def test_adding_a_sheet_again_does_not_disturb_its_size():
    state = layout.resize(layout.place(desk_with("a"), "a", 0, 0), "a", 800, 600)

    state = layout.add_sheet(state, "a")

    assert (layout.sheet(state, "a")["w"], layout.sheet(state, "a")["h"]) == (800, 600)


# --- move, resize, z-order ------------------------------------------------


def test_a_sheet_moves_to_where_it_is_dragged():
    state = layout.move(placed("a"), "a", -400, 250)

    assert (layout.sheet(state, "a")["x"], layout.sheet(state, "a")["y"]) == (-400, 250)


def test_a_sheet_resizes_by_its_corner():
    state = layout.resize(placed("a"), "a", 640, 480)

    assert (layout.sheet(state, "a")["w"], layout.sheet(state, "a")["h"]) == (640, 480)


def test_a_sheet_cannot_be_resized_below_a_usable_minimum():
    state = layout.resize(placed("a"), "a", 2, 2)

    sheet = layout.sheet(state, "a")
    assert sheet["w"] >= layout.MIN_SIZE
    assert sheet["h"] >= layout.MIN_SIZE


def test_raising_a_sheet_puts_it_above_the_others():
    state = placed("a", "b", "c")

    state = layout.raise_sheet(state, "a")

    z = {sid: layout.sheet(state, sid)["z"] for sid in ("a", "b", "c")}
    assert z["a"] > z["b"] and z["a"] > z["c"]


def test_placing_a_sheet_raises_it():
    state = layout.place(desk_with("a", "b"), "a", 0, 0)
    state = layout.place(state, "b", 100, 0)

    assert layout.sheet(state, "b")["z"] > layout.sheet(state, "a")["z"]


# --- piles ----------------------------------------------------------------


def test_dragging_one_sheet_onto_another_forms_a_pile_of_two():
    state = layout.pile(placed("a", "b"), "b", onto="a")

    pile_id = layout.sheet(state, "b")["pile"]
    assert pile_id is not None
    assert layout.sheet(state, "a")["pile"] == pile_id
    assert layout.pile_members(state, pile_id) == ["a", "b"]
    assert layout.pile_count(state, pile_id) == 2


def test_a_pile_sits_where_the_sheet_it_was_dropped_onto_sat():
    state = layout.place(desk_with("a", "b"), "a", 300, 400)
    state = layout.place(state, "b", -50, 20)

    state = layout.pile(state, "b", onto="a")

    pile = layout.pile_of(state, "a")
    assert (pile["x"], pile["y"]) == (300, 400)


def test_dropping_a_third_sheet_onto_a_pile_joins_that_pile():
    state = layout.pile(placed("a", "b", "c"), "b", onto="a")

    state = layout.pile(state, "c", onto="a")

    pile_id = layout.sheet(state, "a")["pile"]
    assert layout.pile_members(state, pile_id) == ["a", "b", "c"]
    assert len(layout.piles(state)) == 1


def test_a_pile_moves_as_a_unit():
    state = layout.pile(placed("a", "b"), "b", onto="a")
    pile_id = layout.sheet(state, "a")["pile"]

    state = layout.move_pile(state, pile_id, 700, -200)

    pile = layout.pile_of(state, "a")
    assert (pile["x"], pile["y"]) == (700, -200)


def test_clicking_a_pile_fans_it_open_and_clicking_again_collapses_it():
    state = layout.pile(placed("a", "b"), "b", onto="a")
    pile_id = layout.sheet(state, "a")["pile"]

    state = layout.toggle_pile(state, pile_id)
    assert layout.pile_of(state, "a")["open"] is True

    state = layout.toggle_pile(state, pile_id)
    assert layout.pile_of(state, "a")["open"] is False


def test_a_sheet_pulled_out_of_a_pile_lands_where_it_was_dropped():
    state = layout.pile(placed("a", "b", "c"), "b", onto="a")
    state = layout.pile(state, "c", onto="a")

    state = layout.unpile(state, "b", 500, 500)

    sheet = layout.sheet(state, "b")
    assert sheet["pile"] is None
    assert (sheet["x"], sheet["y"]) == (500, 500)
    assert sheet["inbox"] is False


def test_the_rest_of_the_pile_is_left_intact_when_one_sheet_is_pulled_out():
    state = layout.pile(placed("a", "b", "c"), "b", onto="a")
    state = layout.pile(state, "c", onto="a")
    pile_id = layout.sheet(state, "a")["pile"]

    state = layout.unpile(state, "b", 500, 500)

    assert layout.pile_members(state, pile_id) == ["a", "c"]


def test_a_pile_of_two_reduced_to_one_stops_being_a_pile():
    state = layout.pile(placed("a", "b"), "b", onto="a")
    pile_id = layout.sheet(state, "a")["pile"]
    state = layout.move_pile(state, pile_id, 250, 250)

    state = layout.unpile(state, "b", 900, 900)

    assert layout.piles(state) == {}
    left = layout.sheet(state, "a")
    assert left["pile"] is None
    assert left["inbox"] is False
    assert (left["x"], left["y"]) == (250, 250)


def test_placing_a_sheet_that_is_in_a_pile_pulls_it_out():
    state = layout.pile(placed("a", "b", "c"), "b", onto="a")
    state = layout.pile(state, "c", onto="a")

    state = layout.place(state, "c", 10, 10)

    assert layout.sheet(state, "c")["pile"] is None
    pile_id = layout.sheet(state, "a")["pile"]
    assert layout.pile_members(state, pile_id) == ["a", "b"]


def test_piling_a_sheet_straight_from_the_inbox_takes_it_out_of_the_inbox():
    state = layout.place(desk_with("a", "b"), "a", 0, 0)

    state = layout.pile(state, "b", onto="a")

    assert layout.sheet(state, "b")["inbox"] is False
    assert layout.inbox(state) == []


def test_a_sheet_cannot_be_piled_onto_itself():
    with pytest.raises(layout.LayoutError):
        layout.pile(placed("a"), "a", onto="a")


# --- trash and restore ----------------------------------------------------


def test_a_trashed_sheet_leaves_the_desk():
    state = layout.remove_sheet(placed("a", "b"), "a")

    assert "a" not in state["sheets"]
    assert "b" in state["sheets"]


def test_trashing_a_sheet_out_of_a_pile_leaves_the_pile_intact():
    state = layout.pile(placed("a", "b", "c"), "b", onto="a")
    state = layout.pile(state, "c", onto="a")
    pile_id = layout.sheet(state, "a")["pile"]

    state = layout.remove_sheet(state, "b")

    assert layout.pile_members(state, pile_id) == ["a", "c"]


def test_trashing_down_to_one_dissolves_the_pile():
    state = layout.pile(placed("a", "b"), "b", onto="a")

    state = layout.remove_sheet(state, "b")

    assert layout.piles(state) == {}
    assert layout.sheet(state, "a")["pile"] is None


def test_trashing_a_sheet_in_the_inbox_removes_it():
    state = layout.remove_sheet(desk_with("a"), "a")

    assert layout.inbox(state) == []


def test_a_restored_sheet_comes_back_to_the_inbox():
    state = layout.remove_sheet(placed("a"), "a")

    state = layout.add_sheet(state, "a")

    assert layout.inbox(state) == ["a"]


# --- viewport -------------------------------------------------------------


def test_the_viewport_remembers_where_the_user_left_it():
    state = layout.set_viewport(layout.empty_state(), -300, 120, 0.5)

    assert layout.viewport(state) == {"x": -300, "y": 120, "scale": 0.5}


def test_moving_the_viewport_never_moves_a_sheet_in_desk_coordinates():
    state = placed("a")
    before = layout.sheet(state, "a")

    state = layout.set_viewport(state, -1000, 4000, 3.0)

    assert layout.sheet(state, "a") == before


# --- geometry the overview and the fan both need --------------------------


def test_a_pile_fans_by_a_step_that_follows_its_sheets():
    small = layout.fan_offset(1, 200, 100)
    large = layout.fan_offset(1, 800, 400)

    assert large[0] > small[0] and large[1] > small[1]


def test_the_first_sheet_in_a_fan_sits_on_the_pile_itself():
    assert layout.fan_offset(0, 400, 300) == (0, 0)


def test_a_fan_step_shows_enough_of_the_sheet_underneath_to_grab_it():
    dx, dy = layout.fan_offset(1, 400, 300)

    assert 80 <= dx <= 320, "a fan that overlaps too far hides what is in the pile"
    assert dy > 0, "the fan must offset vertically too, or the title bars stack"


def test_an_empty_desk_has_no_bounds():
    assert layout.bounds(desk_with("a", "b")) is None, "sheets in the inbox are not on the desk"


def test_bounds_covers_every_placed_sheet():
    state = layout.place(desk_with("a", "b"), "a", 100, 200)
    state = layout.resize(state, "a", 300, 100)
    state = layout.place(state, "b", -50, 0)
    state = layout.resize(state, "b", 100, 100)

    assert layout.bounds(state) == {"x": -50, "y": 0, "w": 450, "h": 300}


def test_bounds_covers_a_pile():
    state = layout.place(desk_with("a", "b"), "a", 400, 400)
    state = layout.resize(state, "a", 200, 200)
    state = layout.place(state, "b", 0, 0)
    state = layout.pile(state, "b", onto="a")

    box = layout.bounds(state)
    assert (box["x"], box["y"]) == (400, 400)
    assert box["w"] >= 200 and box["h"] >= 200


def test_an_open_pile_takes_up_more_room_than_a_closed_one():
    state = layout.place(desk_with("a", "b", "c"), "a", 0, 0)
    state = layout.place(state, "b", 0, 0)
    state = layout.place(state, "c", 0, 0)
    state = layout.pile(state, "b", onto="a")
    state = layout.pile(state, "c", onto="a")
    closed = layout.bounds(state)

    pile_id = layout.sheet(state, "a")["pile"]
    opened = layout.bounds(layout.toggle_pile(state, pile_id))

    assert opened["w"] > closed["w"] and opened["h"] > closed["h"]


# --- adversarial: purity and reading a damaged layout back ----------------


def piled(*sheet_ids):
    state = placed(*sheet_ids)
    for sid in sheet_ids[1:]:
        state = layout.pile(state, sid, onto=sheet_ids[0])
    return state


TRANSITIONS = {
    "add_sheet a new sheet": lambda s: layout.add_sheet(s, "z"),
    "add_sheet a sheet the desk already has": lambda s: layout.add_sheet(s, "a"),
    "place": lambda s: layout.place(s, "b", 1, 2),
    "move": lambda s: layout.move(s, "a", 9, 9),
    "resize": lambda s: layout.resize(s, "a", 500, 500),
    "raise_sheet": lambda s: layout.raise_sheet(s, "a"),
    "set_viewport": lambda s: layout.set_viewport(s, 1, 2, 3),
    "pile": lambda s: layout.pile(s, "d", onto="a"),
    "unpile": lambda s: layout.unpile(s, "b", 5, 5),
    "move_pile": lambda s: layout.move_pile(s, layout.sheet(s, "a")["pile"], 7, 7),
    "toggle_pile": lambda s: layout.toggle_pile(s, layout.sheet(s, "a")["pile"]),
    "close_all_piles": lambda s: layout.close_all_piles(s),
    "remove_sheet": lambda s: layout.remove_sheet(s, "b"),
    "remove_sheet a sheet the desk does not have": lambda s: layout.remove_sheet(s, "ghost"),
    "prune that drops a sheet": lambda s: layout.prune(s, ["a", "b"]),
    "prune that drops nothing": lambda s: layout.prune(s, ["a", "b", "c", "d"]),
}


def a_desk_with_everything_on_it():
    state = piled("a", "b", "c")
    state = layout.toggle_pile(state, layout.sheet(state, "a")["pile"])
    return layout.add_sheet(state, "d")


@pytest.mark.parametrize("name", sorted(TRANSITIONS))
def test_a_transition_never_hands_back_a_view_onto_the_state_it_was_given(name):
    state = a_desk_with_everything_on_it()
    snapshot = json.dumps(state, sort_keys=True)

    out = TRANSITIONS[name](state)
    for entry in out["sheets"].values():
        entry["x"] = 999999
    for entry in out["piles"].values():
        entry["members"] = []
    out["viewport"]["scale"] = 999

    assert json.dumps(state, sort_keys=True) == snapshot


@pytest.mark.parametrize("name", sorted(TRANSITIONS))
def test_a_transition_leaves_the_state_it_was_given_untouched(name):
    state = a_desk_with_everything_on_it()
    snapshot = json.dumps(state, sort_keys=True)

    TRANSITIONS[name](state)

    assert json.dumps(state, sort_keys=True) == snapshot


def test_a_desk_state_survives_a_round_trip_through_json_unchanged():
    state = a_desk_with_everything_on_it()

    assert layout.repair(json.loads(json.dumps(state))) == state


def test_anything_that_is_not_a_desk_state_reads_back_as_an_empty_desk():
    for damage in [None, [], "nope", 7, {"sheets": "nope"}, {"sheets": [1, 2]}]:
        assert layout.repair(damage) == layout.empty_state()


def test_a_sheet_entry_that_lost_its_fields_reads_back_as_a_placeable_sheet():
    state = layout.repair({"sheets": {"a": {}}})

    entry = layout.sheet(state, "a")
    assert (entry["x"], entry["y"]) == (0, 0)
    assert entry["w"] >= layout.MIN_SIZE and entry["h"] >= layout.MIN_SIZE
    assert entry["pile"] is None


def test_a_position_that_is_not_a_finite_number_reads_back_as_the_origin():
    state = layout.repair(
        {"sheets": {"a": {"x": float("nan"), "y": float("inf"), "w": "wide", "h": None}}}
    )

    entry = layout.sheet(state, "a")
    assert (entry["x"], entry["y"]) == (0, 0)
    assert (entry["w"], entry["h"]) == (layout.DEFAULT_WIDTH, layout.DEFAULT_HEIGHT)


def test_a_pile_that_lost_its_members_is_not_a_pile_any_more():
    state = layout.repair(
        {
            "sheets": {"a": {"pile": "pile-1"}, "b": {"pile": "pile-1"}},
            "piles": {"pile-1": {"members": ["a", "ghost"]}},
        }
    )

    assert layout.piles(state) == {}
    assert layout.sheet(state, "a")["pile"] is None
    assert layout.sheet(state, "b")["pile"] is None


def test_a_repaired_pile_and_its_members_agree_about_each_other():
    state = layout.repair(
        {
            "sheets": {"a": {"pile": "pile-1"}, "b": {}, "c": {"pile": "pile-9"}},
            "piles": {"pile-1": {"members": ["a", "b"], "x": 5, "y": 6}},
        }
    )

    assert layout.pile_members(state, "pile-1") == ["a", "b"]
    assert layout.sheet(state, "b")["pile"] == "pile-1"
    assert layout.sheet(state, "b")["inbox"] is False
    assert layout.sheet(state, "c")["pile"] is None


def test_a_repaired_desk_never_hands_out_a_z_or_a_pile_id_that_is_already_taken():
    state = layout.repair(
        {
            "sheets": {"a": {"z": 40, "pile": "pile-7"}, "b": {"z": 41, "pile": "pile-7"}},
            "piles": {"pile-7": {"members": ["a", "b"], "z": 99}},
            "next_z": 1,
            "next_pile": 1,
        }
    )

    state = layout.add_sheet(state, "c")
    assert layout.sheet(state, "c")["z"] > 99
    state = layout.place(state, "c", 0, 0)
    state = layout.add_sheet(state, "d")
    state = layout.place(state, "d", 0, 0)
    state = layout.pile(state, "d", onto="c")
    assert layout.sheet(state, "d")["pile"] != "pile-7"
