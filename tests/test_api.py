"""Seam 1 — the HTTP API.

These tests drive the whole server through HTTP: store, versions, watcher,
tombstones, debounce. Nothing below this seam is tested directly.
"""

from conftest import PNG, SVG


# --- 01: walking skeleton -------------------------------------------------


def test_publishing_a_figure_creates_a_sheet(desk, figures):
    fig = figures / "scatter.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 200, resp.text
    sheet = resp.json()["sheet"]
    assert sheet["source_path"] == str(fig)
    assert sheet["kind"] == "svg"
    assert sheet["version"] == 1
    assert desk.sheet_for(fig) is not None


def test_state_lists_every_published_sheet(desk, figures):
    one = figures / "one.svg"
    one.write_text(SVG.format(color="red"))
    two = figures / "two.png"
    two.write_bytes(PNG)
    desk.publish(one)
    desk.publish(two)

    kinds = {s["source_path"]: s["kind"] for s in desk.sheets()}

    assert kinds == {str(one): "svg", str(two): "png"}


def test_the_desk_page_is_served(desk):
    resp = desk.get("/")

    assert resp.status == 200
    assert "text/html" in resp.headers["Content-Type"]
    assert "desk.js" in resp.text


def test_sheet_content_is_served_with_its_own_type(desk, figures):
    fig = figures / "scatter.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert resp.status == 200
    assert "image/svg+xml" in resp.headers["Content-Type"]
    assert resp.text == SVG.format(color="red")


def test_the_store_copies_so_deleting_the_source_leaves_the_sheet_intact(desk, figures):
    fig = figures / "scatter.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    fig.unlink()

    assert desk.sheet_for(fig) is not None
    resp = desk.get(sheet["content_url"])
    assert resp.status == 200
    assert resp.text == SVG.format(color="red")


def test_publishing_a_path_that_does_not_exist_fails_loudly(desk, figures):
    resp = desk.publish(figures / "nope.svg")

    assert resp.status == 400
    assert "nope.svg" in resp.json()["error"]


def test_sheets_survive_a_server_restart(desk, figures):
    fig = figures / "scatter.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    desk.restart()

    sheet = desk.sheet_for(fig)
    assert sheet is not None
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


# --- 02: sheet identity and versions --------------------------------------


def test_publishing_an_unknown_source_path_creates_a_sheet_at_version_1(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    sheet = desk.publish(fig).json()["sheet"]

    assert sheet["version"] == 1
    assert sheet["versions"] == [1]


def test_publishing_a_known_source_path_appends_a_version_to_the_same_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = desk.publish(fig).json()["sheet"]

    fig.write_text(SVG.format(color="blue"))
    second = desk.publish(fig).json()["sheet"]

    assert second["id"] == first["id"]
    assert second["version"] == 2
    assert len(desk.sheets()) == 1


def test_a_new_version_serves_the_new_content_and_keeps_the_old_one(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = desk.publish(fig).json()["sheet"]
    fig.write_text(SVG.format(color="blue"))
    second = desk.publish(fig).json()["sheet"]

    assert desk.get(second["content_url"]).text == SVG.format(color="blue")
    assert desk.get(f"/api/content/{first['id']}/1").text == SVG.format(color="red")


def test_a_new_version_leaves_position_and_size_untouched(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]
    sid = sheet["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 640, "y": -120})
    desk.post("/api/layout", {"op": "resize", "sheet_id": sid, "w": 900, "h": 700})
    before = desk.state()["layout"]["sheets"][sid]

    fig.write_text(SVG.format(color="blue"))
    desk.publish(fig)

    after = desk.state()["layout"]["sheets"][sid]
    assert (after["x"], after["y"]) == (640, -120)
    assert (after["w"], after["h"]) == (900, 700)
    assert after["inbox"] is False
    assert after == before


def test_version_history_caps_at_twenty_and_the_oldest_is_evicted(desk, figures):
    fig = figures / "fit.svg"
    for i in range(21):
        fig.write_text(SVG.format(color=f"#{i:06x}"))
        sheet = desk.publish(fig).json()["sheet"]

    assert sheet["version"] == 21
    assert sheet["versions"] == list(range(2, 22))
    assert len(sheet["versions"]) == 20
    assert desk.get(f"/api/content/{sheet['id']}/1").status == 404
    assert desk.get(f"/api/content/{sheet['id']}/2").status == 200


def test_a_disallowed_extension_is_rejected_with_a_clear_error(desk, figures):
    fig = figures / "data.csv"
    fig.write_text("a,b\n1,2\n")

    resp = desk.publish(fig)

    assert resp.status == 400
    error = resp.json()["error"]
    assert ".csv" in error
    assert ".svg" in error and ".png" in error and ".md" in error
    assert desk.sheets() == []


def test_a_dotfile_is_rejected(desk, figures):
    fig = figures / ".hidden.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 400
    assert "dotfile" in resp.json()["error"]
    assert desk.sheets() == []


def test_a_tmp_path_is_rejected(desk, figures):
    fig = figures / "figure_tmp01.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 400
    assert "_tmp" in resp.json()["error"]
    assert desk.sheets() == []


def test_every_allowed_extension_is_accepted(desk, figures):
    files = {
        "a.svg": SVG.format(color="red").encode(),
        "b.png": PNG,
        "c.pdf": b"%PDF-1.4\n%fake\n",
        "d.html": b"<!doctype html><p>hi</p>",
        "e.md": b"# hi\n",
    }
    for name, data in files.items():
        (figures / name).write_bytes(data)
        assert desk.publish(figures / name).status == 200, name

    assert len(desk.sheets()) == 5


def test_two_different_paths_with_the_same_name_are_two_sheets(desk, figures):
    (figures / "run1").mkdir()
    (figures / "run2").mkdir()
    one = figures / "run1" / "fit.svg"
    two = figures / "run2" / "fit.svg"
    one.write_text(SVG.format(color="red"))
    two.write_text(SVG.format(color="blue"))

    desk.publish(one)
    desk.publish(two)

    assert len(desk.sheets()) == 2


def test_a_relative_path_is_refused_rather_than_guessed_at(desk, figures):
    """Resolving it would use the *server's* working directory, so the same
    text would name different files to the caller and to the desk — and a
    sheet's identity is the absolute source path."""
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.post("/api/publish", {"source_path": "figures/fit.svg"})

    assert resp.status == 400
    assert "absolute" in resp.json()["error"]
    assert desk.sheets() == []


def test_the_same_absolute_path_written_twice_is_one_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = desk.publish(fig).json()["sheet"]

    second = desk.post("/api/publish", {"source_path": str(fig)}).json()["sheet"]

    assert second["id"] == first["id"]
    assert len(desk.sheets()) == 1


# --- 03: live updates over SSE --------------------------------------------


def test_the_event_stream_is_an_sse_stream(desk):
    with desk.events() as stream:
        assert "text/event-stream" in stream.content_type


def test_a_new_sheet_emits_a_created_event(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    with desk.events() as stream:
        desk.publish(fig)
        event = stream.await_event("sheet.created")

    assert event["sheet"]["source_path"] == str(fig)
    assert event["sheet"]["version"] == 1


def test_a_new_version_emits_a_version_event_carrying_the_new_content_url(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = desk.publish(fig).json()["sheet"]

    with desk.events() as stream:
        fig.write_text(SVG.format(color="blue"))
        desk.publish(fig)
        event = stream.await_event("sheet.version")

    assert event["sheet"]["id"] == first["id"]
    assert event["sheet"]["version"] == 2
    assert event["sheet"]["content_url"] != first["content_url"]
    assert desk.get(event["sheet"]["content_url"]).text == SVG.format(color="blue")


def test_a_version_event_carries_no_layout_so_nothing_can_move(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sheet["id"], "x": 300, "y": 400})

    with desk.events() as stream:
        fig.write_text(SVG.format(color="blue"))
        desk.publish(fig)
        event = stream.await_event("sheet.version")

    assert "layout" not in event
    after = desk.state()["layout"]["sheets"][sheet["id"]]
    assert (after["x"], after["y"]) == (300, 400)


def test_the_stream_tells_the_browser_how_soon_to_reconnect(desk):
    """EventSource reconnects on its own; the server sets the interval."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", desk.port, timeout=5)
    conn.request("GET", "/api/events")
    resp = conn.getresponse()
    head = b""
    while b"\n\n" not in head:
        head += resp.read(1)
    conn.close()

    assert head.startswith(b"retry:")


def test_two_open_pages_both_receive_the_event(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    with desk.events() as one, desk.events() as two:
        desk.publish(fig)
        assert one.await_event("sheet.created")["sheet"]["source_path"] == str(fig)
        assert two.await_event("sheet.created")["sheet"]["source_path"] == str(fig)


# --- 04: implicit watching ------------------------------------------------


def test_changing_a_published_file_creates_a_version_with_no_second_publish(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    fig.write_text(SVG.format(color="blue"))

    desk.await_condition(
        lambda s: any(x["id"] == sheet["id"] and x["version"] == 2 for x in s["sheets"]),
        what="version 2 from the watcher",
    )
    assert desk.get(f"/api/content/{sheet['id']}/2").text == SVG.format(color="blue")


def test_changing_a_watched_file_emits_an_sse_event(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    with desk.events() as stream:
        fig.write_text(SVG.format(color="blue"))
        event = stream.await_event("sheet.version")

    assert event["sheet"]["version"] == 2


def test_a_change_leaves_position_and_size_untouched(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 111, "y": 222})
    desk.post("/api/layout", {"op": "resize", "sheet_id": sid, "w": 555, "h": 444})
    before = desk.state()["layout"]["sheets"][sid]

    fig.write_text(SVG.format(color="blue"))
    desk.await_condition(
        lambda s: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
        what="version 2",
    )

    assert desk.state()["layout"]["sheets"][sid] == before


def test_a_burst_of_writes_debounces_into_a_single_version(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    deadline = time.time() + 1.0
    i = 0
    while time.time() < deadline:
        i += 1
        fig.write_text(SVG.format(color=f"#{i:06x}"))
        time.sleep(0.02)

    desk.await_condition(
        lambda s: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
        what="the debounced version",
    )
    time.sleep(0.6)
    sheet = [x for x in desk.sheets() if x["id"] == sid][0]
    assert sheet["version"] == 2, f"burst of {i} writes produced {sheet['version']} versions"


def test_a_file_that_was_never_published_is_never_picked_up(desk, figures):
    import time

    published = figures / "fit.svg"
    published.write_text(SVG.format(color="red"))
    desk.publish(published)

    stranger = figures / "stranger.svg"
    stranger.write_text(SVG.format(color="green"))
    time.sleep(0.8)
    stranger.write_text(SVG.format(color="black"))
    time.sleep(0.8)

    assert [s["source_path"] for s in desk.sheets()] == [str(published)]


def test_watches_survive_a_server_restart(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    desk.restart()

    fig.write_text(SVG.format(color="blue"))
    desk.await_condition(
        lambda s: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
        what="a version created after restart",
    )


def test_deleting_a_watched_source_file_does_not_damage_the_sheet(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    fig.unlink()
    time.sleep(0.8)

    assert desk.sheet_for(fig) is not None
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


def test_a_watched_file_rewritten_with_identical_bytes_makes_no_new_version(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    fig.write_text(SVG.format(color="red"))
    time.sleep(0.9)

    sheet = [x for x in desk.sheets() if x["id"] == sid][0]
    assert sheet["version"] == 1


# --- 10: trash, tombstones, restore ---------------------------------------


def test_trashing_a_sheet_takes_it_off_the_desk_and_into_the_trash(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    assert desk.post("/api/trash", {"sheet_id": sid}).status == 200

    state = desk.state()
    assert state["sheets"] == []
    assert [s["id"] for s in state["trash"]] == [sid]
    assert sid not in state["layout"]["sheets"]


def test_a_trashed_sheet_keeps_its_content_for_the_trash_corner(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]
    desk.post("/api/trash", {"sheet_id": sheet["id"]})

    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


def test_trashing_stops_watching_so_a_file_change_does_not_resurrect_it(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/trash", {"sheet_id": sid})

    fig.write_text(SVG.format(color="blue"))
    time.sleep(1.0)

    state = desk.state()
    assert state["sheets"] == []
    assert state["trash"][0]["version"] == 1


def test_an_explicit_publish_clears_the_tombstone_and_returns_it_to_the_inbox(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 500, "y": 500})
    desk.post("/api/trash", {"sheet_id": sid})

    fig.write_text(SVG.format(color="blue"))
    sheet = desk.publish(fig).json()["sheet"]

    assert sheet["id"] == sid
    assert sheet["version"] == 2
    state = desk.state()
    assert [s["id"] for s in state["sheets"]] == [sid]
    assert state["trash"] == []
    assert state["layout"]["sheets"][sid]["inbox"] is True


def test_a_restored_sheet_comes_back_to_the_inbox_with_its_content(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]
    desk.post("/api/trash", {"sheet_id": sheet["id"]})

    resp = desk.post("/api/restore", {"sheet_id": sheet["id"]})

    assert resp.status == 200
    state = desk.state()
    assert [s["id"] for s in state["sheets"]] == [sheet["id"]]
    assert state["trash"] == []
    assert state["layout"]["sheets"][sheet["id"]]["inbox"] is True
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


def test_a_restored_sheet_is_watched_again(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/trash", {"sheet_id": sid})
    desk.post("/api/restore", {"sheet_id": sid})

    fig.write_text(SVG.format(color="blue"))

    desk.await_condition(
        lambda s: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
        what="a version after restore",
    )


def test_tombstones_survive_a_server_restart(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/trash", {"sheet_id": sid})

    desk.restart()

    fig.write_text(SVG.format(color="blue"))
    time.sleep(1.0)
    state = desk.state()
    assert state["sheets"] == []
    assert [s["id"] for s in state["trash"]] == [sid]


def test_trashing_emits_an_event_so_an_open_page_drops_the_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    with desk.events() as stream:
        desk.post("/api/trash", {"sheet_id": sid})
        event = stream.await_event("sheet.trashed")

    assert event["sheet_id"] == sid


def test_trashing_a_sheet_the_desk_does_not_have_is_a_clear_404(desk):
    resp = desk.post("/api/trash", {"sheet_id": "nosuchsheet"})

    assert resp.status == 404


# --- 11: remaining renderers ----------------------------------------------


def test_a_png_sheet_is_served_as_a_png(desk, figures):
    fig = figures / "raster.png"
    fig.write_bytes(PNG)
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert resp.headers["Content-Type"] == "image/png"
    assert resp.body == PNG


def test_a_pdf_sheet_is_served_as_a_pdf(desk, figures):
    fig = figures / "paper.pdf"
    fig.write_bytes(b"%PDF-1.4\n%fake\n")
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.body.startswith(b"%PDF")


def test_a_self_contained_html_plot_is_served_verbatim_for_its_iframe(desk, figures):
    plot = '<!doctype html><html><body><div id="plot"></div><script>window.x=1</script></body></html>'
    fig = figures / "plotly.html"
    fig.write_text(plot)
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert "text/html" in resp.headers["Content-Type"]
    assert resp.text == plot


def test_markdown_is_rendered_to_html_server_side(desk, figures):
    fig = figures / "notes.md"
    fig.write_text("# Findings\n\n- the fit is good\n- the residuals are not\n")
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert "text/html" in resp.headers["Content-Type"]
    assert "<h1" in resp.text and "Findings" in resp.text
    assert "<li>the fit is good</li>" in resp.text


def test_a_markdown_table_renders_as_a_table(desk, figures):
    fig = figures / "notes.md"
    fig.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n")
    sheet = desk.publish(fig).json()["sheet"]

    assert "<table>" in desk.get(sheet["content_url"]).text


def test_an_unreadable_file_fails_visibly_on_its_own_sheet(desk, figures):
    fig = figures / "notes.md"
    fig.write_bytes(b"\xff\xfe\x00 not utf-8 at all \xc3\x28")
    sheet = desk.publish(fig).json()["sheet"]

    resp = desk.get(sheet["content_url"])

    assert resp.status == 200, "one bad file must not break the desk"
    assert "desk-unreadable" in resp.text
    assert "not valid UTF-8" in resp.text
    assert desk.sheet_for(fig) is not None


def test_every_kind_updates_in_place_like_an_image_sheet(desk, figures):
    kinds = {
        "a.svg": (SVG.format(color="red").encode(), SVG.format(color="blue").encode()),
        "b.png": (PNG, PNG + b"\x00"),
        "c.pdf": (b"%PDF-1.4\nv1\n", b"%PDF-1.4\nv2\n"),
        "d.html": (b"<p>one</p>", b"<p>two</p>"),
        "e.md": (b"# one\n", b"# two\n"),
    }
    ids = {}
    for name, (first, _) in kinds.items():
        (figures / name).write_bytes(first)
        sheet = desk.publish(figures / name).json()["sheet"]
        ids[name] = sheet["id"]
        desk.post("/api/layout", {"op": "place", "sheet_id": sheet["id"], "x": 10, "y": 20})

    for name, (_, second) in kinds.items():
        (figures / name).write_bytes(second)

    for name, sid in ids.items():
        desk.await_condition(
            lambda s, sid=sid: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
            what=f"{name} version 2",
        )
        placement = desk.state()["layout"]["sheets"][sid]
        assert (placement["x"], placement["y"]) == (10, 20), name


# --- 05 / 06 / 07 / 09: layout reaches disk through the API ---------------


def _publish_three(desk, figures):
    ids = []
    for name in ("a.svg", "b.svg", "c.svg"):
        (figures / name).write_text(SVG.format(color="red"))
        ids.append(desk.publish(figures / name).json()["sheet"]["id"])
    return ids


def test_a_new_sheet_lands_in_the_inbox_never_on_the_desk(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    sid = desk.publish(fig).json()["sheet"]["id"]

    assert desk.state()["layout"]["sheets"][sid]["inbox"] is True


def test_placing_moving_and_resizing_survive_a_restart(desk, figures):
    a, b, _ = _publish_three(desk, figures)
    desk.post("/api/layout", {"op": "place", "sheet_id": a, "x": 300, "y": -80})
    desk.post("/api/layout", {"op": "resize", "sheet_id": a, "w": 720, "h": 540})

    desk.restart()

    sheets = desk.state()["layout"]["sheets"]
    assert (sheets[a]["x"], sheets[a]["y"]) == (300, -80)
    assert (sheets[a]["w"], sheets[a]["h"]) == (720, 540)
    assert sheets[a]["inbox"] is False
    assert sheets[b]["inbox"] is True, "an unplaced sheet is still in the inbox"


def test_pile_membership_survives_a_restart(desk, figures):
    a, b, c = _publish_three(desk, figures)
    for sid in (a, b, c):
        desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 0, "y": 0})
    desk.post("/api/layout", {"op": "pile", "sheet_id": b, "onto": a})
    desk.post("/api/layout", {"op": "pile", "sheet_id": c, "onto": a})

    desk.restart()

    layout = desk.state()["layout"]
    pile_id = layout["sheets"][a]["pile"]
    assert pile_id is not None
    assert layout["piles"][pile_id]["members"] == [a, b, c]


def test_the_viewport_survives_a_reload(desk):
    desk.post("/api/layout", {"op": "viewport", "x": -1200, "y": 640, "scale": 0.4})

    desk.restart()

    assert desk.state()["layout"]["viewport"] == {"x": -1200, "y": 640, "scale": 0.4}


def test_a_layout_op_for_an_unknown_sheet_is_a_clear_error(desk):
    resp = desk.post("/api/layout", {"op": "move", "sheet_id": "ghost", "x": 1, "y": 2})

    assert resp.status == 400
    assert "ghost" in resp.json()["error"]


def test_an_unknown_layout_op_is_a_clear_error(desk):
    resp = desk.post("/api/layout", {"op": "levitate", "sheet_id": "x"})

    assert resp.status == 400
    assert "levitate" in resp.json()["error"]


def test_a_placed_sheet_is_not_sent_back_to_the_inbox_by_a_restart(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 42, "y": 42})

    desk.restart()
    fig.write_text(SVG.format(color="blue"))
    desk.await_condition(
        lambda s: any(x["id"] == sid and x["version"] == 2 for x in s["sheets"]),
        what="version 2 after restart",
    )

    placement = desk.state()["layout"]["sheets"][sid]
    assert placement["inbox"] is False
    assert (placement["x"], placement["y"]) == (42, 42)


def test_the_event_stream_cannot_be_fetched_with_head(desk):
    """A HEAD would subscribe and then hold a server thread on an endless
    stream until a write happened to fail."""
    resp = desk.request("HEAD", "/api/events")

    assert resp.status == 405


def test_static_serving_cannot_escape_the_web_directory(desk):
    for probe in (
        "/../store.py",
        "/../../desk/store.py",
        "/%2e%2e/store.py",
        "/../../../etc/passwd",
    ):
        resp = desk.get(probe)
        assert resp.status == 404, f"{probe} returned {resp.status}"


# --- 13: the /desk command ------------------------------------------------

from conftest import run_desk  # noqa: E402


def test_desk_present_puts_a_named_figure_on_the_desk(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    proc = run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode == 0, proc.stderr
    assert "fit.svg v1" in proc.stdout
    assert f"http://127.0.0.1:{desk.port}" in proc.stdout
    assert desk.sheet_for(fig) is not None


def test_desk_present_with_no_arguments_takes_the_newest_figure(desk, figures):
    import time

    old = figures / "old.svg"
    old.write_text(SVG.format(color="red"))
    time.sleep(0.05)
    newest = figures / "newest.png"
    newest.write_bytes(PNG)

    proc = run_desk("present", cwd=figures, port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode == 0, proc.stderr
    assert "newest.png v1" in proc.stdout
    assert [s["name"] for s in desk.sheets()] == ["newest.png"]


def test_desk_present_with_no_arguments_ignores_dotfiles_and_tmp_files(desk, figures):
    import time

    real = figures / "figure.svg"
    real.write_text(SVG.format(color="red"))
    time.sleep(0.05)
    (figures / ".sneaky.svg").write_text(SVG.format(color="blue"))
    (figures / "half_tmp.png").write_bytes(PNG)

    proc = run_desk("present", cwd=figures, port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode == 0, proc.stderr
    assert [s["name"] for s in desk.sheets()] == ["figure.svg"]


def test_desk_present_says_so_when_there_is_nothing_to_present(desk, figures):
    proc = run_desk("present", cwd=figures, port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode != 0
    assert "found no figure to present" in proc.stderr
    assert desk.sheets() == []


def test_desk_present_rejects_a_file_the_desk_does_not_take(desk, figures):
    fig = figures / "table.csv"
    fig.write_text("a,b\n")

    proc = run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode != 0
    assert "not a desk file type" in proc.stderr
    assert desk.sheets() == []


def test_desk_present_fails_loudly_on_a_path_that_is_not_there(desk, figures):
    proc = run_desk("present", str(figures / "ghost.svg"), port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode != 0
    assert "no such file" in proc.stderr


def test_presenting_the_same_path_again_updates_it_rather_than_duplicating(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)

    fig.write_text(SVG.format(color="blue"))
    proc = run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)

    assert "fit.svg v2" in proc.stdout
    assert "updated in place" in proc.stdout
    assert len(desk.sheets()) == 1


def test_desk_present_starts_the_server_when_the_port_is_closed(tmp_path, figures, free_port):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    data_dir = tmp_path / "cold"

    proc = run_desk(
        "present",
        str(fig),
        port=free_port,
        data_dir=data_dir,
        env={"DESK_LOG_DIR": str(tmp_path / "logs")},
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "starting the server" in proc.stderr
    assert f"http://127.0.0.1:{free_port}" in proc.stdout

    status = run_desk("status", port=free_port, data_dir=data_dir)
    assert status.returncode == 0
    assert "1 sheets" in status.stdout


def test_desk_status_says_when_the_desk_is_not_running(free_port, tmp_path):
    proc = run_desk("status", port=free_port, data_dir=tmp_path / "nowhere")

    assert proc.returncode != 0
    assert "not running" in proc.stdout


# --- adversarial: malformed input, damaged data, path identity ------------

import json as _json  # noqa: E402
import os as _os  # noqa: E402
import socket as _socket  # noqa: E402
import unicodedata as _unicodedata  # noqa: E402

import pytest  # noqa: E402


def strict_json(body: bytes):
    """Parse the way a browser's JSON.parse does: NaN and Infinity are not JSON."""

    def refuse(token):
        raise AssertionError(f"the desk served {token}, which JSON.parse rejects")

    return _json.loads(body, parse_constant=refuse)


def test_a_position_that_is_not_a_number_is_refused(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    resp = desk.post("/api/layout", {"op": "move", "sheet_id": sid, "x": "left", "y": 0})

    assert resp.status == 400
    assert "x" in resp.json()["error"]
    assert desk.state()["layout"]["sheets"][sid]["x"] == 0


def test_a_position_that_is_not_finite_never_reaches_the_desk(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    for value in (float("nan"), float("inf"), float("-inf")):
        assert desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": value, "y": 0}).status == 400

    strict_json(desk.get("/api/state").body)


def test_a_desk_whose_layout_is_not_readable_json_serves_a_desk_the_browser_can_parse(
    desk, figures
):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.stop()
    (desk.data_dir / "layout.json").write_text('{"sheets": {"' + sid + '": {"x": NaN}}}')
    desk.start()

    strict_json(desk.get("/api/state").body)
    assert desk.sheet_for(fig) is not None


def test_a_desk_whose_layout_file_is_damaged_still_starts_with_every_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    for damage in ['{"sheets": {"a": ', "null", "[1, 2]", '{"sheets": "nope"}', '{"sheets": {"a": {}}}']:
        desk.stop()
        (desk.data_dir / "layout.json").write_text(damage)
        desk.start()

        assert desk.sheet_for(fig) is not None, damage
        assert desk.get("/api/state").status == 200, damage


def test_a_desk_whose_sheet_index_is_damaged_still_starts(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    for damage in ['{"sheets": [{"id": "abc"', "null", "[]", '{"sheets": [{"id": "abc"}]}',
                   '{"sheets": [{"id": "abc", "source_path": 5, "kind": "svg", "versions": []}]}']:
        desk.stop()
        (desk.data_dir / "sheets.json").write_text(damage)
        desk.start()

        assert desk.get("/api/state").status == 200, damage
        assert desk.publish(fig).status == 200, damage


def test_a_content_url_naming_a_version_that_is_not_a_number_is_a_404(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    for bad in ("banana", "-1", "1.5", "%C2%B2"):
        resp = desk.get(f"/api/content/{sheet['id']}/{bad}")
        assert resp.status == 404, f"{bad} -> {resp.status} {resp.text[:80]}"


def test_a_content_url_may_only_be_cached_forever_when_it_names_a_version(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    versioned = desk.get(sheet["content_url"])
    unversioned = desk.get(f"/api/content/{sheet['id']}")

    assert "immutable" in versioned.headers["Cache-Control"]
    assert "immutable" not in unversioned.headers["Cache-Control"]


def test_a_request_body_that_is_not_an_object_is_a_clear_error(desk):
    for body in ([1, 2, 3], None, "hello", 5):
        for endpoint in ("/api/publish", "/api/layout", "/api/trash", "/api/restore"):
            resp = desk.post(endpoint, body)
            assert resp.status == 400, f"{endpoint} {body!r} -> {resp.status} {resp.text[:80]}"


def test_publishing_something_that_is_not_a_path_is_a_clear_error(desk):
    for value in (12, {"a": 1}, ["a"], True):
        resp = desk.post("/api/publish", {"source_path": value})

        assert resp.status == 400, f"{value!r} -> {resp.status} {resp.text[:80]}"
        assert "source_path" in resp.json()["error"]


def test_a_layout_op_that_is_not_a_name_is_a_clear_error(desk):
    for op in (["move"], {"op": "move"}, 5):
        resp = desk.post("/api/layout", {"op": op})

        assert resp.status == 400, f"{op!r} -> {resp.status} {resp.text[:80]}"


def test_a_size_that_is_not_a_number_is_refused(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    resp = desk.post("/api/layout", {"op": "resize", "sheet_id": sid, "w": "big", "h": None})

    assert resp.status == 400
    assert desk.get("/api/state").status == 200


def test_a_request_whose_length_header_is_nonsense_still_gets_an_answer(desk):
    sock = _socket.create_connection(("127.0.0.1", desk.port), 5)
    sock.settimeout(5)
    sock.sendall(
        b"POST /api/publish HTTP/1.1\r\nHost: desk\r\n"
        b"Content-Type: application/json\r\nContent-Length: abc\r\n\r\n{}"
    )
    try:
        reply = sock.recv(4096)
    finally:
        sock.close()

    assert reply.startswith(b"HTTP/1.1 400"), reply[:80]
    assert desk.get("/api/state").status == 200


def test_publishing_a_path_the_filesystem_cannot_use_fails_loudly(desk):
    for bad in ("/" + "x" * 5000 + ".svg", "/tmp/a\x00b.svg"):
        resp = desk.post("/api/publish", {"source_path": bad})

        assert resp.status == 400, f"{bad[:20]}... -> {resp.status} {resp.text[:120]}"
        assert desk.sheets() == []


def test_two_spellings_of_one_file_are_one_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    shouting = figures / "FIT.SVG"
    if not _os.path.exists(shouting):
        pytest.skip("this filesystem is case-sensitive, so these are two files")

    first = desk.publish(fig).json()["sheet"]
    second = desk.publish(shouting).json()["sheet"]

    assert second["id"] == first["id"]
    assert second["version"] == 2
    assert len(desk.sheets()) == 1


def test_a_path_in_either_unicode_normal_form_is_one_sheet(desk, figures):
    composed = figures / _unicodedata.normalize("NFC", "café.svg")
    composed.write_text(SVG.format(color="red"))
    decomposed = figures / _unicodedata.normalize("NFD", "café.svg")
    if not _os.path.exists(decomposed):
        pytest.skip("this filesystem keeps the two normal forms apart")

    first = desk.publish(composed).json()["sheet"]
    second = desk.publish(decomposed).json()["sheet"]

    assert second["id"] == first["id"]
    assert len(desk.sheets()) == 1


def at_version(fig, n):
    return lambda state: any(
        s["source_path"] == str(fig) and s["version"] == n for s in state["sheets"]
    )


def test_a_file_saved_by_rename_makes_a_version_even_if_its_mtime_is_unchanged(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)
    fig.write_text(SVG.format(color="blue"))
    desk.await_condition(at_version(fig, 2), what="the watcher to settle on version 2")
    stamp = _os.stat(fig)

    replacement = figures / "fit.svg.new"
    replacement.write_text(SVG.format(color="cyan"))  # same length, so same size
    _os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    _os.replace(replacement, fig)

    desk.await_condition(
        at_version(fig, 3), what="a version from a save-by-rename that kept the mtime"
    )


def test_a_restart_does_not_change_which_event_a_later_publish_emits(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 300, "y": 200})
    desk.restart()

    with desk.events() as stream:
        fig.write_text(SVG.format(color="blue"))
        event = stream.await_event("sheet.version")

    assert event["sheet"]["version"] == 2
    assert "layout" not in event
    placement = desk.state()["layout"]["sheets"][sid]
    assert (placement["x"], placement["y"], placement["inbox"]) == (300, 200, False)


def test_a_watched_file_replaced_by_a_directory_leaves_the_sheet_intact(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    fig.unlink()
    fig.mkdir()
    (fig / "inner.svg").write_text(SVG.format(color="blue"))

    desk.await_condition(lambda s: s["sheets"], what="the sheet to still be there")
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")
    assert desk.get("/api/state").status == 200


def test_publishing_the_same_path_from_many_threads_at_once_makes_one_sheet(desk, figures):
    import threading

    fig = figures / "race.svg"
    fig.write_text(SVG.format(color="red"))
    replies = []
    threads = [
        threading.Thread(target=lambda: replies.append(desk.publish(fig))) for _ in range(8)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [r.status for r in replies] == [200] * 8
    sheets = desk.sheets()
    assert len(sheets) == 1
    assert sheets[0]["versions"] == list(range(1, 9))
    for n in sheets[0]["versions"]:
        assert desk.get(f"/api/content/{sheets[0]['id']}/{n}").status == 200


def test_a_figure_inside_a_tmp_directory_is_rejected(desk, figures):
    scratch = figures / "figures_tmp"
    scratch.mkdir()
    fig = scratch / "plot.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 400
    assert "_tmp" in resp.json()["error"]
    assert desk.sheets() == []


def test_a_figure_inside_a_hidden_directory_is_still_a_figure(desk, figures):
    """The dotfile rule is about the file, not every directory above it.

    Agent-produced figures routinely land under a hidden directory somebody
    else chose — `~/.claude/`, `~/.cache/`, a tool's state directory. The user
    did not hide those figures, so the desk does not treat them as hidden. Only
    `*_tmp*`, which exists to catch a half-written `savefig`, reaches up the
    path.
    """
    hidden = figures / ".claude"
    hidden.mkdir()
    fig = hidden / "plot.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 200, resp.text
    assert desk.sheet_for(fig) is not None


def test_a_figure_that_is_itself_a_dotfile_is_rejected(desk, figures):
    fig = figures / ".plot.svg"
    fig.write_text(SVG.format(color="red"))

    resp = desk.publish(fig)

    assert resp.status == 400
    assert "dotfile" in resp.json()["error"]
    assert desk.sheets() == []


def test_a_file_written_into_a_tmp_directory_is_never_picked_up(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)
    scratch = figures / "run_tmp"
    scratch.mkdir()

    (scratch / "fit.svg").write_text(SVG.format(color="blue"))

    assert desk.publish(scratch / "fit.svg").status == 400
    assert len(desk.sheets()) == 1


# --- geometry the page and the overview both need -------------------------


def test_an_empty_desk_reports_no_bounds(desk):
    assert desk.state()["geometry"]["bounds"] is None


def test_the_desk_reports_the_box_containing_everything_placed_on_it(desk, figures):
    ids = []
    for name in ("one.svg", "two.svg"):
        fig = figures / name
        fig.write_text(SVG.format(color="red"))
        ids.append(desk.publish(fig).json()["sheet"]["id"])
    desk.post("/api/layout", {"op": "place", "sheet_id": ids[0], "x": 0, "y": 0})
    resp = desk.post("/api/layout", {"op": "place", "sheet_id": ids[1], "x": 500, "y": 300})

    box = resp.json()["geometry"]["bounds"]

    assert (box["x"], box["y"]) == (0, 0)
    assert box["w"] >= 500 and box["h"] >= 300
    assert desk.state()["geometry"]["bounds"] == box


def test_a_sheet_waiting_in_the_inbox_is_not_part_of_the_desks_bounds(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    assert desk.state()["geometry"]["bounds"] is None


def test_a_pile_takes_up_more_room_on_the_desk_once_it_is_fanned_open(desk, figures):
    ids = []
    for name in ("one.svg", "two.svg", "three.svg"):
        fig = figures / name
        fig.write_text(SVG.format(color="red"))
        ids.append(desk.publish(fig).json()["sheet"]["id"])
    for sid in ids:
        desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 0, "y": 0})
    for sid in ids[1:]:
        desk.post("/api/layout", {"op": "pile", "sheet_id": sid, "onto": ids[0]})
    closed = desk.state()["geometry"]["bounds"]

    pile_id = desk.state()["layout"]["sheets"][ids[0]]["pile"]
    opened = desk.post("/api/layout", {"op": "toggle_pile", "pile_id": pile_id}).json()["geometry"]["bounds"]

    assert opened["w"] > closed["w"] and opened["h"] > closed["h"]


def test_the_desk_hands_the_page_the_constants_it_lays_a_fan_out_with(desk):
    geometry = desk.state()["geometry"]

    assert 0 < geometry["fan_step"]["x"] < 1
    assert 0 < geometry["fan_step"]["y"] < 1
    assert geometry["default_size"]["w"] > geometry["min_size"]
    assert geometry["default_size"]["h"] > geometry["min_size"]


def test_geometry_is_derived_and_never_written_into_the_saved_layout(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 40, "y": 60})

    desk.restart()

    state = desk.state()
    assert "geometry" not in state["layout"]
    assert state["geometry"]["bounds"]["x"] == 40


def test_every_event_that_carries_a_layout_carries_the_geometry_that_goes_with_it(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    with desk.events() as stream:
        sid = desk.publish(fig).json()["sheet"]["id"]
        created = stream.await_event("sheet.created")
        desk.post("/api/trash", {"sheet_id": sid})
        trashed = stream.await_event("sheet.trashed")
        desk.post("/api/restore", {"sheet_id": sid})
        restored = stream.await_event("sheet.restored")

    for event in (created, trashed, restored):
        assert "geometry" in event, event["type"]
        assert "bounds" in event["geometry"]
