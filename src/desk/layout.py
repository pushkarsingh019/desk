"""The layout model — where every sheet sits on the desk.

Desk state is a plain object: position, size, z-order, pile membership, inbox
membership, viewport. Every transition here is a pure function of that state.
Nothing in this module touches the filesystem, the network, or the DOM.

Layout authority is exclusively the user's. Nothing in this module auto-places,
auto-tiles, or reflows: `add_sheet` puts a sheet in the inbox and only `place`
takes it out.
"""

from __future__ import annotations

import copy
import math

#: A newly created sheet's size on the desk, in desk coordinates.
DEFAULT_WIDTH = 360
DEFAULT_HEIGHT = 280

#: A sheet never shrinks below this, or it becomes impossible to grab again.
MIN_SIZE = 60

#: How far a fanned-open pile spreads each member, as a fraction of the sheet.
#: A fan has to show enough of the sheet underneath to recognise and grab it,
#: so the step follows the sheets rather than being a fixed number of pixels.
FAN_STEP_X = 0.62
FAN_STEP_Y = 0.22


class LayoutError(KeyError):
    """A transition was asked to act on a sheet or pile the desk does not have."""


# --- state ----------------------------------------------------------------


def empty_state() -> dict:
    return {
        "sheets": {},
        "piles": {},
        "next_z": 1,
        "next_pile": 1,
        "viewport": {"x": 0, "y": 0, "scale": 1},
    }


def _copy(state: dict) -> dict:
    return copy.deepcopy(state)


def repair(state) -> dict:
    """Read a desk state back from whatever `layout.json` actually contains.

    A damaged layout must never stop the desk from starting, and it must never
    put a value on the desk that the page cannot parse — a position of `NaN`
    serialises to JSON that `JSON.parse` refuses, which would leave the user
    with a blank page and no way back. Everything that reads cleanly is kept;
    everything else falls back to a sane default. Nothing is lost that the
    server cannot put back: a sheet with no place simply returns to the inbox.
    """
    out = empty_state()
    if not isinstance(state, dict):
        return out

    raw_sheets = state.get("sheets")
    if isinstance(raw_sheets, dict):
        for sheet_id, entry in raw_sheets.items():
            if isinstance(sheet_id, str) and isinstance(entry, dict):
                out["sheets"][sheet_id] = {
                    "x": _number(entry.get("x"), 0),
                    "y": _number(entry.get("y"), 0),
                    "w": max(MIN_SIZE, _number(entry.get("w"), DEFAULT_WIDTH)),
                    "h": max(MIN_SIZE, _number(entry.get("h"), DEFAULT_HEIGHT)),
                    "z": int(_number(entry.get("z"), 1)),
                    "inbox": bool(entry.get("inbox", True)),
                    "pile": None,
                }

    raw_piles = state.get("piles")
    if isinstance(raw_piles, dict):
        for pile_id, entry in raw_piles.items():
            if not (isinstance(pile_id, str) and isinstance(entry, dict)):
                continue
            raw_members = entry.get("members")
            if not isinstance(raw_members, list):
                continue
            # A sheet belongs to at most one pile, and a pile of one is not a
            # pile — the same two rules `_leave_pile` keeps.
            members = [
                m
                for m in raw_members
                if isinstance(m, str) and m in out["sheets"] and out["sheets"][m]["pile"] is None
            ]
            if len(members) < 2:
                continue
            out["piles"][pile_id] = {
                "x": _number(entry.get("x"), 0),
                "y": _number(entry.get("y"), 0),
                "z": int(_number(entry.get("z"), 1)),
                "open": bool(entry.get("open")),
                "members": members,
            }
            for member in members:
                out["sheets"][member]["pile"] = pile_id
                out["sheets"][member]["inbox"] = False

    highest_z = max(
        [0]
        + [s["z"] for s in out["sheets"].values()]
        + [p["z"] for p in out["piles"].values()]
    )
    out["next_z"] = max(int(_number(state.get("next_z"), 1)), highest_z + 1)
    taken = [
        int(pile_id.rsplit("-", 1)[-1])
        for pile_id in out["piles"]
        if pile_id.rsplit("-", 1)[-1].isdigit()
    ]
    out["next_pile"] = max([int(_number(state.get("next_pile"), 1)), 1] + [n + 1 for n in taken])

    raw_viewport = state.get("viewport")
    if isinstance(raw_viewport, dict):
        scale = _number(raw_viewport.get("scale"), 1)
        out["viewport"] = {
            "x": _number(raw_viewport.get("x"), 0),
            "y": _number(raw_viewport.get("y"), 0),
            "scale": scale if scale > 0 else 1,
        }
    return out


def _number(value, default):
    """A finite number, or the default. `NaN` and `Infinity` are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value if math.isfinite(value) else default


def _sheet(state: dict, sheet_id: str) -> dict:
    try:
        return state["sheets"][sheet_id]
    except KeyError:
        raise LayoutError(f"no sheet {sheet_id!r} on the desk") from None


def _pile(state: dict, pile_id: str) -> dict:
    try:
        return state["piles"][pile_id]
    except KeyError:
        raise LayoutError(f"no pile {pile_id!r} on the desk") from None


# --- reading --------------------------------------------------------------


def sheet(state: dict, sheet_id: str) -> dict:
    return copy.deepcopy(_sheet(state, sheet_id))


def sheets(state: dict) -> dict:
    return copy.deepcopy(state["sheets"])


def piles(state: dict) -> dict:
    return copy.deepcopy(state["piles"])


def inbox(state: dict) -> list[str]:
    """Sheet ids waiting in the inbox, oldest first."""
    return [sid for sid, s in state["sheets"].items() if s["inbox"]]


def pile_of(state: dict, sheet_id: str) -> dict | None:
    pile_id = _sheet(state, sheet_id)["pile"]
    return copy.deepcopy(state["piles"][pile_id]) if pile_id else None


def pile_members(state: dict, pile_id: str) -> list[str]:
    return list(_pile(state, pile_id)["members"])


def pile_count(state: dict, pile_id: str) -> int:
    return len(_pile(state, pile_id)["members"])


def viewport(state: dict) -> dict:
    return dict(state["viewport"])


# --- transitions ----------------------------------------------------------


def add_sheet(state: dict, sheet_id: str, w: int = DEFAULT_WIDTH, h: int = DEFAULT_HEIGHT) -> dict:
    """Give the desk a sheet it does not have yet. It lands in the inbox.

    A sheet the desk already knows is left exactly where the user put it — this
    is what makes a new version update in place instead of jumping home.
    """
    if sheet_id in state["sheets"]:
        return _copy(state)
    out = _copy(state)
    out["sheets"][sheet_id] = {
        "x": 0,
        "y": 0,
        "w": w,
        "h": h,
        "z": out["next_z"],
        "inbox": True,
        "pile": None,
    }
    out["next_z"] += 1
    return out


def place(state: dict, sheet_id: str, x: float, y: float) -> dict:
    """Move a sheet out of the inbox (or out of a pile) onto the desk surface."""
    out = _leave_pile(_copy(state), sheet_id)
    s = _sheet(out, sheet_id)
    s["inbox"] = False
    s["x"] = x
    s["y"] = y
    return _raise(out, sheet_id)


def move(state: dict, sheet_id: str, x: float, y: float) -> dict:
    out = _copy(state)
    s = _sheet(out, sheet_id)
    s["x"] = x
    s["y"] = y
    return out


def resize(state: dict, sheet_id: str, w: float, h: float) -> dict:
    out = _copy(state)
    s = _sheet(out, sheet_id)
    s["w"] = max(MIN_SIZE, w)
    s["h"] = max(MIN_SIZE, h)
    return out


def raise_sheet(state: dict, sheet_id: str) -> dict:
    return _raise(_copy(state), sheet_id)


def _raise(out: dict, sheet_id: str) -> dict:
    s = _sheet(out, sheet_id)
    if s["pile"]:
        out["piles"][s["pile"]]["z"] = out["next_z"]
    else:
        s["z"] = out["next_z"]
    out["next_z"] += 1
    return out


def set_viewport(state: dict, x: float, y: float, scale: float) -> dict:
    out = _copy(state)
    out["viewport"] = {"x": x, "y": y, "scale": scale}
    return out


# --- piles ----------------------------------------------------------------


def pile(state: dict, sheet_id: str, onto: str) -> dict:
    """Drag one sheet onto another. Joins the target's pile, or starts one."""
    if sheet_id == onto:
        raise LayoutError("a sheet cannot be piled onto itself")
    out = _copy(state)
    target = _sheet(out, onto)
    _sheet(out, sheet_id)  # loud if the dragged sheet is unknown

    out = _leave_pile(out, sheet_id)
    target = _sheet(out, onto)

    pile_id = target["pile"]
    if pile_id is None:
        pile_id = f"pile-{out['next_pile']}"
        out["next_pile"] += 1
        out["piles"][pile_id] = {
            "x": target["x"],
            "y": target["y"],
            "z": target["z"],
            "open": False,
            "members": [onto],
        }
        target["pile"] = pile_id
        target["inbox"] = False

    dragged = _sheet(out, sheet_id)
    dragged["pile"] = pile_id
    dragged["inbox"] = False
    out["piles"][pile_id]["members"].append(sheet_id)
    return _raise(out, onto)


def unpile(state: dict, sheet_id: str, x: float, y: float) -> dict:
    """Pull a single sheet back out of a pile and drop it on the desk."""
    if _sheet(state, sheet_id)["pile"] is None:
        raise LayoutError(f"sheet {sheet_id!r} is not in a pile")
    return place(state, sheet_id, x, y)


def move_pile(state: dict, pile_id: str, x: float, y: float) -> dict:
    out = _copy(state)
    p = _pile(out, pile_id)
    p["x"] = x
    p["y"] = y
    return out


def toggle_pile(state: dict, pile_id: str) -> dict:
    out = _copy(state)
    p = _pile(out, pile_id)
    p["open"] = not p["open"]
    if p["open"]:
        p["z"] = out["next_z"]
        out["next_z"] += 1
    return out


def close_all_piles(state: dict) -> dict:
    out = _copy(state)
    for p in out["piles"].values():
        p["open"] = False
    return out


def _leave_pile(out: dict, sheet_id: str) -> dict:
    """Detach a sheet from whatever pile it is in, dissolving a pile of one."""
    s = _sheet(out, sheet_id)
    pile_id = s["pile"]
    if pile_id is None:
        return out
    p = out["piles"][pile_id]
    p["members"] = [m for m in p["members"] if m != sheet_id]
    s["pile"] = None
    if len(p["members"]) <= 1:
        for last in p["members"]:
            leftover = out["sheets"][last]
            leftover["pile"] = None
            leftover["inbox"] = False
            leftover["x"] = p["x"]
            leftover["y"] = p["y"]
            leftover["z"] = p["z"]
        del out["piles"][pile_id]
    return out


# --- trash ----------------------------------------------------------------


def remove_sheet(state: dict, sheet_id: str) -> dict:
    """Throw a sheet away — from the desk, the inbox, or a pile."""
    if sheet_id not in state["sheets"]:
        return _copy(state)
    out = _leave_pile(_copy(state), sheet_id)
    del out["sheets"][sheet_id]
    return out


def prune(state: dict, keep_ids) -> dict:
    """Drop layout entries for sheets the store no longer has live."""
    keep = set(keep_ids)
    out = _copy(state)
    for sheet_id in list(state["sheets"]):
        if sheet_id not in keep:
            out = remove_sheet(out, sheet_id)
    return out


# --- geometry the page and the overview both need -------------------------


def fan_offset(index: int, w: float = DEFAULT_WIDTH, h: float = DEFAULT_HEIGHT) -> tuple[float, float]:
    """Where the nth member of a fanned-open pile sits, relative to the pile."""
    return (index * w * FAN_STEP_X, index * h * FAN_STEP_Y)


def bounds(state: dict) -> dict | None:
    """The box containing everything placed on the desk, for the overview.

    Returns None when nothing has been placed yet.
    """
    boxes = []
    for sid, s in state["sheets"].items():
        if s["inbox"]:
            continue
        if s["pile"]:
            continue
        boxes.append((s["x"], s["y"], s["w"], s["h"]))
    for pile_id, p in state["piles"].items():
        members = p["members"]
        w = max((state["sheets"][m]["w"] for m in members), default=DEFAULT_WIDTH)
        h = max((state["sheets"][m]["h"] for m in members), default=DEFAULT_HEIGHT)
        if p["open"]:
            dx, dy = fan_offset(len(members) - 1, w, h)
            w += dx
            h += dy
        boxes.append((p["x"], p["y"], w, h))
    if not boxes:
        return None
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}
