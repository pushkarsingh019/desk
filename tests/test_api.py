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
    assert desk.get(f"/api/content/main/{first['id']}/1").text == SVG.format(color="red")


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
    assert desk.get(f"/api/content/main/{sheet['id']}/1").status == 404
    assert desk.get(f"/api/content/main/{sheet['id']}/2").status == 200


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
    assert desk.get(f"/api/content/main/{sheet['id']}/2").text == SVG.format(color="blue")


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


def test_clearing_the_desk_throws_every_sheet_into_the_trash(desk, figures):
    placed, waiting = figures / "placed.svg", figures / "waiting.svg"
    placed.write_text(SVG.format(color="red"))
    waiting.write_text(SVG.format(color="blue"))
    placed_id = desk.publish(placed).json()["sheet"]["id"]
    waiting_id = desk.publish(waiting).json()["sheet"]["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": placed_id, "x": 500, "y": 500})

    resp = desk.post("/api/clear", {})

    assert resp.status == 200
    assert sorted(resp.json()["trashed"]) == sorted([placed_id, waiting_id])
    state = desk.state()
    assert state["sheets"] == []
    assert sorted(s["id"] for s in state["trash"]) == sorted([placed_id, waiting_id])
    assert state["layout"]["sheets"] == {}


def test_a_cleared_sheet_is_not_resurrected_by_a_file_change_but_can_be_restored(desk, figures):
    import time

    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    desk.post("/api/clear", {})

    fig.write_text(SVG.format(color="blue"))
    time.sleep(1.0)
    assert desk.state()["sheets"] == []

    desk.post("/api/restore", {"sheet_id": sid})
    assert [s["id"] for s in desk.state()["sheets"]] == [sid]


def test_clearing_emits_one_event_so_an_open_page_empties(desk, figures):
    for name in ("one.svg", "two.svg"):
        (figures / name).write_text(SVG.format(color="red"))
        desk.publish(figures / name)

    with desk.events() as stream:
        desk.post("/api/clear", {})
        event = stream.await_event("desk.cleared")

    assert len(event["trashed"]) == 2
    assert event["layout"]["sheets"] == {}


def test_clearing_an_empty_desk_is_harmless(desk):
    resp = desk.post("/api/clear", {})

    assert resp.status == 200
    assert resp.json()["trashed"] == []


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
    (desk.desk_dir / "layout.json").write_text('{"sheets": {"' + sid + '": {"x": NaN}}}')
    desk.start()

    strict_json(desk.get("/api/state").body)
    assert desk.sheet_for(fig) is not None


def test_a_desk_whose_layout_file_is_damaged_still_starts_with_every_sheet(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    for damage in ['{"sheets": {"a": ', "null", "[1, 2]", '{"sheets": "nope"}', '{"sheets": {"a": {}}}']:
        desk.stop()
        (desk.desk_dir / "layout.json").write_text(damage)
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
        (desk.desk_dir / "sheets.json").write_text(damage)
        desk.start()

        assert desk.get("/api/state").status == 200, damage
        assert desk.publish(fig).status == 200, damage


def test_a_content_url_naming_a_version_that_is_not_a_number_is_a_404(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    for bad in ("banana", "-1", "1.5", "%C2%B2"):
        resp = desk.get(f"/api/content/main/{sheet['id']}/{bad}")
        assert resp.status == 404, f"{bad} -> {resp.status} {resp.text[:80]}"


def test_a_content_url_may_only_be_cached_forever_when_it_names_a_version(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]

    versioned = desk.get(sheet["content_url"])
    unversioned = desk.get(f"/api/content/main/{sheet['id']}")

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
        assert desk.get(f"/api/content/main/{sheets[0]['id']}/{n}").status == 200


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


# --- 16: someone else's desk ----------------------------------------------

import time as _time  # noqa: E402

from conftest import _free_port  # noqa: E402

ALICE_PATH = "/Users/alice/figs/fit.svg"


def _closed(port: int) -> bool:
    with _socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _by_id(desk, sheet_id):
    return next(s for s in desk.sheets() if s["id"] == sheet_id)


def test_a_figure_sent_from_another_machine_lands_in_the_inbox_marked_with_its_origin(desk):
    resp = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode())

    assert resp.status == 200, resp.text
    sheet = resp.json()["sheet"]
    assert sheet["origin"] == "alice-mac"
    assert sheet["source_path"] == ALICE_PATH
    assert sheet["name"] == "fit.svg"
    assert sheet["kind"] == "svg"
    assert sheet["version"] == 1
    state = desk.state()
    assert [s["id"] for s in state["sheets"]] == [sheet["id"]]
    assert state["layout"]["sheets"][sheet["id"]]["inbox"] is True
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


def test_a_sheet_published_here_has_no_origin(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    assert desk.publish(fig).json()["sheet"]["origin"] is None


def test_sending_the_same_file_again_updates_that_sheet_in_place(desk):
    first = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]
    sid = first["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 640, "y": 300})
    desk.post("/api/layout", {"op": "resize", "sheet_id": sid, "w": 900, "h": 700})
    before = desk.state()["layout"]["sheets"][sid]

    second = desk.send("alice-mac", ALICE_PATH, SVG.format(color="blue").encode()).json()["sheet"]

    assert second["id"] == sid
    assert second["version"] == 2
    assert len(desk.sheets()) == 1
    assert desk.state()["layout"]["sheets"][sid] == before
    assert desk.get(second["content_url"]).text == SVG.format(color="blue")


def test_the_same_path_sent_from_two_machines_is_two_sheets(desk):
    alice = desk.send("alice-mac", "/tmp/fit.svg", SVG.format(color="red").encode()).json()["sheet"]
    bob = desk.send("bob-mac", "/tmp/fit.svg", SVG.format(color="blue").encode()).json()["sheet"]

    assert alice["id"] != bob["id"]
    assert sorted(s["origin"] for s in desk.sheets()) == ["alice-mac", "bob-mac"]


def test_a_sent_sheet_and_a_local_sheet_at_the_same_path_are_two_sheets(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    local = desk.publish(fig).json()["sheet"]

    sent = desk.send("alice-mac", str(fig), SVG.format(color="blue").encode()).json()["sheet"]

    assert local["id"] != sent["id"]
    assert desk.get(local["content_url"]).text == SVG.format(color="red")
    assert desk.get(sent["content_url"]).text == SVG.format(color="blue")


def test_a_sent_sheet_is_never_watched_even_when_that_path_exists_here(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sent = desk.send("alice-mac", str(fig), SVG.format(color="blue").encode()).json()["sheet"]

    fig.write_text(SVG.format(color="green"))
    _time.sleep(1.0)

    assert _by_id(desk, sent["id"])["version"] == 1
    assert desk.get(sent["content_url"]).text == SVG.format(color="blue")

    desk.restart()
    fig.write_text(SVG.format(color="black"))
    _time.sleep(1.0)

    again = _by_id(desk, sent["id"])
    assert again["version"] == 1
    assert again["origin"] == "alice-mac"
    assert desk.get(again["content_url"]).text == SVG.format(color="blue")


def test_a_sent_sheet_the_user_threw_away_comes_back_when_it_is_sent_again(desk):
    first = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]
    desk.post("/api/trash", {"sheet_id": first["id"]})
    assert desk.sheets() == []

    second = desk.send("alice-mac", ALICE_PATH, SVG.format(color="blue").encode()).json()["sheet"]

    assert second["id"] == first["id"]
    assert second["version"] == 2
    state = desk.state()
    assert [s["id"] for s in state["sheets"]] == [first["id"]]
    assert state["trash"] == []
    assert state["layout"]["sheets"][first["id"]]["inbox"] is True


def test_sending_emits_a_created_event_that_names_the_origin(desk):
    with desk.events() as stream:
        desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode())
        event = stream.await_event("sheet.created")

    assert event["sheet"]["origin"] == "alice-mac"
    assert event["sheet"]["name"] == "fit.svg"
    assert event["layout"]["sheets"][event["sheet"]["id"]]["inbox"] is True


@pytest.mark.parametrize("origin", ["", "alice mac", "alice:mac", "-alice", 7, None])
def test_a_send_without_a_usable_origin_is_refused(desk, origin):
    import base64

    body = {"source_path": ALICE_PATH, "content": base64.b64encode(b"<svg/>").decode()}
    if origin is not None:
        body["origin"] = origin

    resp = desk.post("/api/publish", body)

    assert resp.status == 400
    assert "origin" in resp.json()["error"]
    assert desk.sheets() == []


@pytest.mark.parametrize("content", ["not base64!!", 12, None, ["QQ=="]])
def test_a_send_whose_content_is_not_the_files_bytes_is_refused(desk, content):
    resp = desk.post("/api/publish", {"origin": "alice-mac", "source_path": ALICE_PATH, "content": content})

    assert resp.status == 400
    assert "base64" in resp.json()["error"]
    assert desk.sheets() == []


def test_a_send_of_a_file_type_the_desk_does_not_take_is_refused(desk):
    resp = desk.send("alice-mac", "/Users/alice/table.csv", b"a,b\n")

    assert resp.status == 400
    assert "not a desk file type" in resp.json()["error"]
    assert desk.sheets() == []


def test_a_send_without_a_path_is_refused(desk):
    import base64

    resp = desk.post("/api/publish", {"origin": "alice-mac", "content": base64.b64encode(b"<svg/>").decode()})

    assert resp.status == 400
    assert desk.sheets() == []


def test_a_sent_sheet_keeps_its_origin_across_a_restart(desk):
    sent = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]

    desk.restart()

    [sheet] = desk.sheets()
    assert sheet["id"] == sent["id"]
    assert sheet["origin"] == "alice-mac"
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")


# -- the desk command, sending ---------------------------------------------

#: Send under the plain hostname: with no Tailscale CLI to ask, the name is
#: deterministic and the command does not wait on one.
NO_TAILSCALE = {"DESK_TAILSCALE_CLI": "/nonexistent/tailscale"}


def test_desk_present_to_puts_the_figure_on_the_other_desk_and_nothing_here(desk, figures, free_port, tmp_path):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    proc = run_desk(
        "present", str(fig), "--to", f"127.0.0.1:{desk.port}",
        port=free_port, data_dir=tmp_path / "never", env={**NO_TAILSCALE, "DESK_LOG_DIR": str(tmp_path / "logs")},
    )

    assert proc.returncode == 0, proc.stderr
    assert "fit.svg v1" in proc.stdout
    assert "waiting in 127.0.0.1's inbox" in proc.stdout
    assert f"http://127.0.0.1:{desk.port}" in proc.stdout
    [sheet] = desk.sheets()
    assert sheet["origin"]
    assert sheet["source_path"] == fig.as_posix()
    assert desk.get(sheet["content_url"]).text == SVG.format(color="red")
    # Nothing was started or published on the sending machine.
    assert "starting the server" not in proc.stderr
    assert _closed(free_port)
    assert not (tmp_path / "never").exists()


def test_desk_present_to_sends_under_the_same_name_every_time(desk, figures, free_port):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = run_desk("present", str(fig), "--to", f"http://127.0.0.1:{desk.port}", port=free_port, env=NO_TAILSCALE)
    assert first.returncode == 0, first.stderr

    fig.write_text(SVG.format(color="blue"))
    second = run_desk("present", str(fig), "--to", f"http://127.0.0.1:{desk.port}", port=free_port, env=NO_TAILSCALE)

    assert second.returncode == 0, second.stderr
    assert "fit.svg v2" in second.stdout
    assert "updated in place on 127.0.0.1's desk" in second.stdout
    [sheet] = desk.sheets()
    assert sheet["version"] == 2
    assert desk.get(sheet["content_url"]).text == SVG.format(color="blue")


def test_desk_present_to_an_unreachable_desk_fails_loudly_and_starts_nothing(figures, free_port, tmp_path):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    nobody = _free_port()

    proc = run_desk(
        "present", str(fig), "--to", f"127.0.0.1:{nobody}",
        port=free_port, data_dir=tmp_path / "never", env=NO_TAILSCALE,
    )

    assert proc.returncode != 0
    assert "could not reach 127.0.0.1's desk" in proc.stderr
    assert "starting the server" not in proc.stderr
    assert _closed(free_port)
    assert _closed(nobody)


def test_desk_present_to_refuses_a_file_the_other_desk_does_not_take_by_name(desk, figures, free_port):
    fig = figures / "table.csv"
    fig.write_text("a,b\n")

    proc = run_desk("present", str(fig), "--to", f"127.0.0.1:{desk.port}", port=free_port, env=NO_TAILSCALE)

    assert proc.returncode != 0
    assert "not a desk file type" in proc.stderr
    assert desk.sheets() == []


def test_desk_present_to_needs_a_machine_name_or_a_url(figures, free_port):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    proc = run_desk("present", str(fig), "--to", "bob-mac:notaport", port=free_port, env=NO_TAILSCALE)

    assert proc.returncode != 0
    assert "--to needs a machine name" in proc.stderr
    assert _closed(free_port)


# --- 17: several desks ----------------------------------------------------


def _desks(desk, op, name):
    return desk.post("/api/desks", {"op": op, "name": name})


def test_a_new_desk_starts_with_one_desk_called_main_and_it_is_current(desk):
    state = desk.state()

    assert state["desk"] == "main"
    assert state["desks"] == ["main"]


def test_creating_a_desk_brings_it_out_and_everything_then_lands_on_it(desk, figures):
    on_main = figures / "main.svg"
    on_main.write_text(SVG.format(color="red"))
    desk.publish(on_main)

    resp = _desks(desk, "create", "paper 1")
    assert resp.status == 200, resp.text
    assert resp.json() == {"desk": "paper 1", "desks": ["main", "paper 1"]}
    assert desk.sheets() == []

    later = figures / "later.svg"
    later.write_text(SVG.format(color="blue"))
    on_paper = desk.publish(later).json()["sheet"]
    desk.send("alice-mac", ALICE_PATH, SVG.format(color="green").encode())
    assert sorted(s["name"] for s in desk.sheets()) == ["fit.svg", "later.svg"]
    # The desk's name has a space in it, and the content URL carries it.
    assert desk.get(on_paper["content_url"]).text == SVG.format(color="blue")

    _desks(desk, "switch", "main")
    assert [s["name"] for s in desk.sheets()] == ["main.svg"]


def test_the_same_file_on_two_desks_is_a_sheet_on_each_and_both_keep_updating(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    on_main = desk.publish(fig).json()["sheet"]
    _desks(desk, "create", "paper")
    on_paper = desk.publish(fig).json()["sheet"]
    assert on_paper["content_url"] != on_main["content_url"]

    fig.write_text(SVG.format(color="blue"))
    desk.await_condition(lambda s: s["sheets"] and s["sheets"][0]["version"] == 2, what="paper's copy to update")
    _desks(desk, "switch", "main")
    desk.await_condition(lambda s: s["sheets"] and s["sheets"][0]["version"] == 2, what="main's copy to update")


def test_a_versioned_content_url_names_its_desk_so_two_desks_never_share_one(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    on_main = desk.publish(fig).json()["sheet"]
    _desks(desk, "create", "paper")
    fig.write_text(SVG.format(color="blue"))
    on_paper = desk.publish(fig).json()["sheet"]

    assert on_main["id"] == on_paper["id"]
    assert on_main["content_url"].startswith("/api/content/main/")
    assert on_paper["content_url"].startswith("/api/content/paper/")
    assert desk.get(on_main["content_url"]).text == SVG.format(color="red")
    assert desk.get(on_paper["content_url"]).text == SVG.format(color="blue")
    assert desk.get(f"/api/content/nowhere/{on_main['id']}/1").status == 404


def test_only_the_current_desks_events_reach_the_page(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)
    _desks(desk, "create", "paper")

    with desk.events() as stream:
        fig.write_text(SVG.format(color="blue"))
        _time.sleep(1.0)
        _desks(desk, "switch", "main")
        # Had main's new version reached this page, it would be queued ahead
        # of the switch; the first thing on the stream must be the switch.
        event = stream.next_event()

    assert event["type"] == "desk.changed"
    assert event["desk"] == "main"
    assert event["desks"] == ["main", "paper"]


def test_the_current_desk_survives_a_restart(desk, figures):
    _desks(desk, "create", "paper")
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)

    desk.restart()

    state = desk.state()
    assert state["desk"] == "paper"
    assert state["desks"] == ["main", "paper"]
    assert [s["name"] for s in state["sheets"]] == ["fit.svg"]


def test_a_data_directory_from_before_there_were_desks_becomes_the_main_desk(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sheet = desk.publish(fig).json()["sheet"]
    sid = sheet["id"]
    desk.post("/api/layout", {"op": "place", "sheet_id": sid, "x": 500, "y": 400})
    desk.stop()
    import shutil

    for piece in ("sheets.json", "layout.json", "content"):
        shutil.move(str(desk.desk_dir / piece), str(desk.data_dir / piece))
    shutil.rmtree(desk.data_dir / "desks")
    (desk.data_dir / "current.json").unlink(missing_ok=True)

    desk.start()

    state = desk.state()
    assert state["desk"] == "main"
    assert [s["id"] for s in state["sheets"]] == [sid]
    assert state["layout"]["sheets"][sid]["x"] == 500
    assert desk.get(state["sheets"][0]["content_url"]).text == SVG.format(color="red")
    assert not (desk.data_dir / "sheets.json").exists()


def test_an_empty_desk_can_be_taken_away_but_not_one_with_sheets_or_the_last_one(desk, figures):
    assert _desks(desk, "remove", "main").status == 400

    _desks(desk, "create", "paper")
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    desk.publish(fig)
    refused = _desks(desk, "remove", "paper")
    assert refused.status == 400
    assert "still has sheets" in refused.json()["error"]

    desk.post("/api/clear", {})
    resp = _desks(desk, "remove", "paper")
    assert resp.status == 200, resp.text
    assert resp.json() == {"desk": "main", "desks": ["main"]}
    assert not (desk.data_dir / "desks" / "paper").exists()


@pytest.mark.parametrize("name", ["", " ", "../etc", "a/b", "x" * 41, 7, None, "paper 1 "])
def test_a_desk_name_that_will_not_survive_a_url_or_a_directory_is_refused(desk, name):
    resp = desk.post("/api/desks", {"op": "create", "name": name})

    assert resp.status == 400
    assert desk.state()["desks"] == ["main"]


def test_creating_a_desk_that_exists_or_switching_to_one_that_does_not_is_a_clear_error(desk):
    assert _desks(desk, "create", "main").status == 400
    resp = _desks(desk, "switch", "nowhere")
    assert resp.status == 400
    assert "no desk called" in resp.json()["error"]


def test_desk_status_says_which_desk_is_out(desk, figures):
    _desks(desk, "create", "paper")

    proc = run_desk("status", port=desk.port, data_dir=desk.data_dir)

    assert proc.returncode == 0, proc.stderr
    assert "desk: paper  (also: main)" in proc.stdout


# --- 18: comments on a sheet ----------------------------------------------

from conftest import png  # noqa: E402

#: A matplotlib-style SVG: sized in points, drawn in viewBox units.
PLOT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="460.8pt" height="345.6pt" '
    'viewBox="0 0 460.8 345.6"><rect width="460.8" height="345.6" fill="{color}"/></svg>'
)


def _comment(desk, op, **params):
    return desk.post("/api/comments", {"op": op, **params})


def _sheet_with_svg(desk, figures, name="fit.svg", color="red"):
    fig = figures / name
    fig.write_text(SVG.format(color=color))
    return fig, desk.publish(fig).json()["sheet"]


def test_a_comment_is_pinned_on_the_sheet_and_read_back_in_order(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)

    first = _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, text="axis label is cut off")
    assert first.status == 200, first.text
    second = _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0.5, "y": 0.5, "w": 0, "h": 0}, text="this point")
    assert second.status == 200, second.text

    [back] = desk.sheets()
    assert back["open_comments"] == 2
    one, two = back["comments"]
    assert one["text"] == "axis label is cut off"
    assert one["anchor"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
    assert one["number"] == 1 and two["number"] == 2
    assert two["anchor"] == {"x": 0.5, "y": 0.5, "w": 0, "h": 0}
    assert one["resolved_at"] is None
    assert one["id"] != two["id"]
    assert back["natural_size"] == {"w": 100, "h": 60}


def test_a_comment_is_a_record_on_the_sheet_not_layout(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    desk.post("/api/layout", {"op": "place", "sheet_id": sheet["id"], "x": 300, "y": 200})
    before = desk.state()["layout"]

    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="whole sheet is too busy")

    assert desk.state()["layout"] == before
    index = _json.loads((desk.desk_dir / "sheets.json").read_text())
    [record] = index["sheets"]
    [stored] = record["comments"]
    assert set(stored) == {"id", "version", "anchor", "text", "created_at", "resolved_at"}
    assert stored["anchor"] is None
    assert "comments" not in _json.dumps(before)


def test_a_comment_is_stamped_with_the_version_current_when_it_was_made(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    fig.write_text(SVG.format(color="blue"))
    desk.publish(fig)

    _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0, "y": 0, "w": 1, "h": 1}, text="on v2")
    [back] = desk.sheets()

    assert back["version"] == 2
    assert back["comments"][0]["version"] == 2


def test_a_new_version_resolves_nothing_and_the_pin_stays_where_it_was(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}, text="centre")

    fig.write_text(SVG.format(color="blue"))
    with desk.events() as stream:
        desk.publish(fig)
        event = stream.await_event("sheet.version")

    [back] = desk.sheets()
    assert back["version"] == 2
    assert back["open_comments"] == 1
    [comment] = back["comments"]
    assert comment["version"] == 1
    assert comment["anchor"] == {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
    assert event["sheet"]["comments"][0]["version"] == 1


def test_comments_survive_trash_and_restore_and_a_restart(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0, "y": 0, "w": 0.5, "h": 0.5}, text="keep me")
    desk.post("/api/trash", {"sheet_id": sheet["id"]})

    assert desk.state()["trash"][0]["open_comments"] == 1
    desk.post("/api/restore", {"sheet_id": sheet["id"]})
    desk.restart()

    [back] = desk.sheets()
    assert [c["text"] for c in back["comments"]] == ["keep me"]
    assert back["open_comments"] == 1


def test_a_comment_outlives_the_eviction_of_the_version_it_was_made_on(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0.9, "y": 0.1, "w": 0.1, "h": 0.1}, text="made on v1")

    for i in range(21):
        fig.write_text(SVG.format(color=f"#{i:06x}"))
        desk.publish(fig)

    [back] = desk.sheets()
    assert 1 not in back["versions"]
    [comment] = back["comments"]
    assert comment["version"] == 1
    assert comment["natural_size"] is None, "the version is gone, so there is nothing to measure"
    assert back["natural_size"] == {"w": 100, "h": 60}


def test_resolving_keeps_the_comment_and_removing_drops_it(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    a = _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="a").json()["sheet"]["comments"][0]["id"]
    b = _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="b").json()["sheet"]["comments"][1]["id"]

    resolved = _comment(desk, "resolve", sheet_id=sheet["id"], comment_id=a)
    assert resolved.status == 200, resolved.text
    [back] = desk.sheets()
    assert back["open_comments"] == 1
    assert back["comments"][0]["resolved_at"] is not None
    assert back["comments"][1]["resolved_at"] is None
    # Resolving again is harmless: the comment stays resolved.
    assert _comment(desk, "resolve", sheet_id=sheet["id"], comment_id=a).status == 200

    removed = _comment(desk, "remove", sheet_id=sheet["id"], comment_id=b)
    assert removed.status == 200, removed.text
    [back] = desk.sheets()
    assert [c["id"] for c in back["comments"]] == [a]
    assert back["open_comments"] == 0


def test_a_comment_with_no_anchor_is_about_the_whole_sheet(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)

    resp = _comment(desk, "add", sheet_id=sheet["id"], text="needs a title")

    assert resp.status == 200, resp.text
    [comment] = resp.json()["sheet"]["comments"]
    assert comment["anchor"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"sheet_id": "ghost", "anchor": None, "text": "hi"},
        {"sheet_id": None, "anchor": None, "text": "hi"},
        {"sheet_id": 7, "anchor": None, "text": "hi"},
        {"anchor": None, "text": ""},
        {"anchor": None, "text": "   "},
        {"anchor": None, "text": 5},
        {"anchor": None},
        {"anchor": {"x": 1.2, "y": 0, "w": 0, "h": 0}, "text": "out"},
        {"anchor": {"x": -0.1, "y": 0, "w": 0, "h": 0}, "text": "out"},
        {"anchor": {"x": 0.8, "y": 0, "w": 0.5, "h": 0}, "text": "leaves the box"},
        {"anchor": {"x": 0, "y": 0.8, "w": 0, "h": 0.5}, "text": "leaves the box"},
        {"anchor": {"x": "left", "y": 0, "w": 0, "h": 0}, "text": "not a number"},
        {"anchor": {"x": 0, "y": 0, "w": 0}, "text": "missing h"},
        {"anchor": [0, 0, 1, 1], "text": "not an object"},
    ],
)
def test_a_comment_the_desk_cannot_make_is_refused_with_400(desk, figures, body):
    fig, sheet = _sheet_with_svg(desk, figures)
    body = {"op": "add", "sheet_id": sheet["id"], **body}

    resp = desk.post("/api/comments", body)

    assert resp.status == 400, resp.text
    assert resp.json()["error"]
    assert desk.sheets()[0]["comments"] == []


def test_resolving_or_removing_a_comment_that_is_not_there_is_400(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)

    for op in ("resolve", "remove"):
        resp = _comment(desk, op, sheet_id=sheet["id"], comment_id="nope")
        assert resp.status == 400, resp.text
        assert "nope" in resp.json()["error"]
    assert _comment(desk, "resolve", sheet_id="ghost", comment_id="nope").status == 400
    assert _comment(desk, "levitate", sheet_id=sheet["id"]).status == 400


def test_an_anchor_on_an_iframe_kind_is_refused_but_a_sheet_level_comment_is_not(desk, figures):
    files = {"a.html": b"<p>hi</p>", "b.md": b"# hi\n", "c.pdf": b"%PDF-1.4\n%fake\n"}
    for name, data in files.items():
        (figures / name).write_bytes(data)
        sheet = desk.publish(figures / name).json()["sheet"]
        assert sheet["natural_size"] is None, name

        refused = _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0, "y": 0, "w": 0, "h": 0}, text="here")
        assert refused.status == 400, name
        assert "sheet-level" in refused.json()["error"]

        allowed = _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="everywhere")
        assert allowed.status == 200, name
        assert allowed.json()["sheet"]["open_comments"] == 1


def test_a_comment_on_a_trashed_sheet_is_refused(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    desk.post("/api/trash", {"sheet_id": sheet["id"]})

    resp = _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="too late")

    assert resp.status == 400
    assert "trash" in resp.json()["error"]


def test_a_comment_can_be_made_on_a_sent_sheet_and_stays_here(desk):
    sent = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]

    resp = _comment(desk, "add", sheet_id=sent["id"], anchor={"x": 0, "y": 0, "w": 0.5, "h": 0.5}, text="for alice")
    assert resp.status == 200, resp.text

    again = desk.send("alice-mac", ALICE_PATH, SVG.format(color="blue").encode()).json()["sheet"]
    assert again["version"] == 2
    assert [c["text"] for c in again["comments"]] == ["for alice"]
    assert again["origin"] == "alice-mac"


def test_one_file_on_two_desks_has_two_independent_comment_sets(desk, figures):
    fig, on_main = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=on_main["id"], anchor=None, text="on main")
    _desks(desk, "create", "paper")
    on_paper = desk.publish(fig).json()["sheet"]

    assert on_paper["comments"] == []
    _comment(desk, "add", sheet_id=on_paper["id"], anchor=None, text="on paper")

    assert [c["text"] for c in desk.sheets()[0]["comments"]] == ["on paper"]
    _desks(desk, "switch", "main")
    assert [c["text"] for c in desk.sheets()[0]["comments"]] == ["on main"]


def test_a_damaged_comment_is_dropped_on_load_and_the_rest_survive(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="fine")
    desk.stop()
    index_path = desk.desk_dir / "sheets.json"
    index = _json.loads(index_path.read_text())
    index["sheets"][0]["comments"] += [
        "not a record",
        {"id": "x"},
        {"id": "y", "version": "one", "anchor": None, "text": "bad version", "created_at": 1, "resolved_at": None},
        {"id": "z", "version": 1, "anchor": {"x": 2, "y": 0, "w": 0, "h": 0}, "text": "bad anchor", "created_at": 1, "resolved_at": None},
        {"id": "w", "version": 1, "anchor": None, "text": "", "created_at": 1, "resolved_at": None},
    ]
    index_path.write_text(_json.dumps(index))
    desk.start()

    [back] = desk.sheets()
    assert [c["text"] for c in back["comments"]] == ["fine"]
    assert desk.publish(fig).status == 200

    desk.stop()
    index = _json.loads(index_path.read_text())
    index["sheets"][0]["comments"] = "nonsense"
    index_path.write_text(_json.dumps(index))
    desk.start()
    assert desk.sheets()[0]["comments"] == []


def test_every_comment_change_emits_the_sheet_and_no_layout(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    desk.post("/api/layout", {"op": "place", "sheet_id": sheet["id"], "x": 300, "y": 400})

    with desk.events() as stream:
        cid = _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="one").json()["sheet"]["comments"][0]["id"]
        added = stream.await_event("sheet.changed")
        _comment(desk, "resolve", sheet_id=sheet["id"], comment_id=cid)
        resolved = stream.await_event("sheet.changed")
        _comment(desk, "remove", sheet_id=sheet["id"], comment_id=cid)
        removed = stream.await_event("sheet.changed")

    assert added["sheet"]["open_comments"] == 1
    assert resolved["sheet"]["open_comments"] == 0
    assert resolved["sheet"]["comments"][0]["resolved_at"] is not None
    assert removed["sheet"]["comments"] == []
    for event in (added, resolved, removed):
        assert "layout" not in event
        assert event["sheet"]["id"] == sheet["id"]
    after = desk.state()["layout"]["sheets"][sheet["id"]]
    assert (after["x"], after["y"]) == (300, 400)


def test_the_natural_size_comes_from_the_stored_bytes(desk, figures):
    raster = figures / "raster.png"
    raster.write_bytes(png(640, 480))
    plot = figures / "plot.svg"
    plot.write_text(PLOT_SVG.format(color="red"))
    broken = figures / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n not really")

    sizes = {}
    for fig in (raster, plot, broken):
        sizes[fig.name] = desk.publish(fig).json()["sheet"]["natural_size"]

    assert sizes["raster.png"] == {"w": 640, "h": 480}
    assert sizes["plot.svg"] == {"w": 460.8, "h": 345.6}
    assert sizes["broken.png"] is None


# --- 19: feedback for the agent -------------------------------------------


def _feedback(desk, *args, cwd=None):
    return run_desk("feedback", *args, cwd=cwd, port=desk.port, data_dir=desk.data_dir)


def test_desk_feedback_reports_every_open_comment_with_where_it_is(desk, figures):
    raster = figures / "raster.png"
    raster.write_bytes(png(640, 480))
    sid = desk.publish(raster).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.75, "y": 0.1, "w": 0.2, "h": 0.1}, text="the legend covers the data")
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.1, "y": 0.5, "w": 0, "h": 0}, text="this outlier\nis it real?")
    done = _comment(desk, "add", sheet_id=sid, anchor=None, text="done already").json()["sheet"]["comments"][2]["id"]
    _comment(desk, "resolve", sheet_id=sid, comment_id=done)
    _comment(desk, "add", sheet_id=sid, anchor=None, text="needs a title")
    quiet = figures / "quiet.svg"
    quiet.write_text(SVG.format(color="red"))
    desk.publish(quiet)
    binned = figures / "binned.svg"
    binned.write_text(SVG.format(color="blue"))
    binned_id = desk.publish(binned).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=binned_id, anchor=None, text="in the trash")
    desk.post("/api/trash", {"sheet_id": binned_id})

    proc = _feedback(desk, cwd=figures)

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert str(raster) in out
    assert "v1 · 3 open comments · 640×480 px" in out
    assert "#1  on v1 — upper right" in out
    assert "fractions: x 0.750–0.950, y 0.100–0.200" in out
    assert "pixels on v1: x 480–608, y 48–96 (of 640×480)" in out
    assert "the legend covers the data" in out
    assert "#2  on v1 — left edge, middle" in out
    assert "fractions: x 0.100, y 0.500 (a point)" in out
    assert "pixels on v1: x 64, y 240 (of 640×480)" in out
    assert "      this outlier\n      is it real?" in out
    assert "#4  on v1 — whole sheet" in out
    assert "needs a title" in out
    assert "done already" not in out, "resolved comments are not feedback"
    assert str(quiet) not in out, "a sheet with nothing open is not reported"
    assert "in the trash" not in out and str(binned) not in out


def test_desk_feedback_json_is_the_same_report_as_one_object(desk, figures):
    raster = figures / "raster.png"
    raster.write_bytes(png(640, 480))
    sid = desk.publish(raster).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.5, "y": 0.5, "w": 0.25, "h": 0.25}, text="here")
    _comment(desk, "add", sheet_id=sid, anchor=None, text="everywhere")

    proc = _feedback(desk, "--json", cwd=figures)

    assert proc.returncode == 0, proc.stderr
    report = _json.loads(proc.stdout)
    assert report["desk"] == "main"
    [sheet] = report["sheets"]
    assert sheet["source_path"] == str(raster)
    assert sheet["origin"] is None
    assert sheet["version"] == 1
    assert sheet["natural_size"] == {"w": 640, "h": 480}
    here, everywhere = sheet["comments"]
    assert set(here) >= {"id", "version", "anchor", "pixels", "location", "text"}
    assert here["anchor"] == {"x": 0.5, "y": 0.5, "w": 0.25, "h": 0.25}
    assert here["pixels"] == {"x": 320, "y": 240, "w": 160, "h": 120}
    assert here["location"] == "centre"
    assert here["text"] == "here"
    assert everywhere["anchor"] is None
    assert everywhere["pixels"] is None
    assert everywhere["location"] == "whole sheet"


def test_desk_feedback_converts_svg_fractions_against_the_viewbox(desk, figures):
    plot = figures / "plot.svg"
    plot.write_text(PLOT_SVG.format(color="red"))
    sid = desk.publish(plot).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.5, "y": 0.25, "w": 0.25, "h": 0.5}, text="x axis")

    proc = _feedback(desk, str(plot), "--json")

    assert proc.returncode == 0, proc.stderr
    [sheet] = _json.loads(proc.stdout)["sheets"]
    assert sheet["natural_size"] == {"w": 460.8, "h": 345.6}
    assert sheet["comments"][0]["pixels"] == {"x": 230, "y": 86, "w": 115, "h": 173}
    text = _feedback(desk, str(plot)).stdout
    assert "460.8×345.6 px" in text
    assert "pixels on v1: x 230–346, y 86–259" in text


def test_desk_feedback_gives_pixels_against_the_commented_version_while_it_is_retained(desk, figures):
    raster = figures / "raster.png"
    raster.write_bytes(png(100, 100))
    sid = desk.publish(raster).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, text="corner")
    raster.write_bytes(png(200, 300))
    desk.publish(raster)

    proc = _feedback(desk, "--json", cwd=figures)
    [sheet] = _json.loads(proc.stdout)["sheets"]
    [comment] = sheet["comments"]

    assert sheet["version"] == 2
    assert comment["version"] == 1
    assert comment["retained"] is True
    assert comment["pixels"] == {"x": 100, "y": 150, "w": 100, "h": 150}
    assert comment["pixels_on_version"] == {"x": 50, "y": 50, "w": 50, "h": 50}
    text = _feedback(desk, cwd=figures).stdout
    assert "#1  on v1 — lower right" in text
    assert "pixels on v2: x 100–200, y 150–300 (of 200×300)" in text
    assert "pixels on v1: x 50–100, y 50–100" in text


def test_desk_feedback_says_when_the_commented_version_is_no_longer_retained(desk, figures):
    raster = figures / "raster.png"
    raster.write_bytes(png(100, 100))
    sid = desk.publish(raster).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0, "y": 0, "w": 0.5, "h": 0.5}, text="early")
    for i in range(21):
        raster.write_bytes(png(200, 200) + bytes([i]))
        desk.publish(raster)

    proc = _feedback(desk, cwd=figures)

    assert proc.returncode == 0, proc.stderr
    assert "#1  on v1, no longer retained — upper left" in proc.stdout
    assert "pixels on v22: x 0–100, y 0–100 (of 200×200)" in proc.stdout
    assert "pixels on v1" not in proc.stdout
    [sheet] = _json.loads(_feedback(desk, "--json", cwd=figures).stdout)["sheets"]
    assert sheet["comments"][0]["retained"] is False
    assert sheet["comments"][0]["pixels_on_version"] is None


def test_desk_feedback_still_reports_fractions_when_the_size_cannot_be_read(desk, figures):
    broken = figures / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n not really a png")
    sid = desk.publish(broken).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, text="blurry")
    notes = figures / "notes.md"
    notes.write_text("# hi\n")
    nid = desk.publish(notes).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=nid, anchor=None, text="shorter")

    proc = _feedback(desk, cwd=figures)

    assert proc.returncode == 0, proc.stderr
    assert "natural size not available" in proc.stdout
    assert "fractions: x 0.100–0.300, y 0.100–0.300" in proc.stdout
    assert "pixels: not available — the desk could not read a width and height from this .png" in proc.stdout
    assert "#1  on v1 — whole sheet" in proc.stdout and "shorter" in proc.stdout


def test_desk_feedback_names_the_origin_of_a_sent_sheet(desk):
    sent = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]
    _comment(desk, "add", sheet_id=sent["id"], anchor={"x": 0.9, "y": 0.9, "w": 0.1, "h": 0.1}, text="for alice")

    proc = _feedback(desk, "--all")

    assert proc.returncode == 0, proc.stderr
    assert ALICE_PATH in proc.stdout
    assert "from alice-mac — a copy" in proc.stdout
    assert "pixels on v1: x 90–100, y 54–60" in proc.stdout
    [sheet] = _json.loads(_feedback(desk, ALICE_PATH, "--json").stdout)["sheets"]
    assert sheet["origin"] == "alice-mac"


def test_desk_feedback_with_a_path_resolves_it_the_way_present_does(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=sid, anchor=None, text="only this one")
    other = figures / "other.svg"
    other.write_text(SVG.format(color="blue"))
    oid = desk.publish(other).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=oid, anchor=None, text="not this one")

    proc = _feedback(desk, "fit.svg", cwd=figures)

    assert proc.returncode == 0, proc.stderr
    assert "only this one" in proc.stdout
    assert "not this one" not in proc.stdout


def test_desk_feedback_with_nothing_open_says_so_and_exits_zero(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    proc = _feedback(desk, cwd=figures)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"no open feedback under {figures} (desk feedback --all for the whole desk)"
    assert _feedback(desk, "--all").stdout.strip() == "no open feedback"

    cid = _comment(desk, "add", sheet_id=sid, anchor=None, text="x").json()["sheet"]["comments"][0]["id"]
    _comment(desk, "resolve", sheet_id=sid, comment_id=cid)
    proc = _feedback(desk, str(fig))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "no open feedback"
    assert _json.loads(_feedback(desk, "--json", cwd=figures).stdout)["sheets"] == []


def test_desk_feedback_is_scoped_to_what_this_agent_put_up(desk, figures, tmp_path):
    """A desk holds many sheets; the user is commenting on the ones the agent
    just made. Bare `desk feedback` is the working directory, newest first;
    the whole desk is `--all`."""
    mine = figures / "fit.svg"
    mine.write_text(SVG.format(color="red"))
    mine_id = desk.publish(mine).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=mine_id, anchor=None, text="mine, older")
    newer = figures / "nested" / "hist.svg"
    newer.parent.mkdir()
    newer.write_text(SVG.format(color="blue"))
    newer_id = desk.publish(newer).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=newer_id, anchor=None, text="mine, newer")
    elsewhere = tmp_path / "other-project" / "old.svg"
    elsewhere.parent.mkdir()
    elsewhere.write_text(SVG.format(color="green"))
    other_id = desk.publish(elsewhere).json()["sheet"]["id"]
    _comment(desk, "add", sheet_id=other_id, anchor=None, text="someone else's project")
    sent = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]
    _comment(desk, "add", sheet_id=sent["id"], anchor=None, text="a copy from alice")

    proc = _feedback(desk, cwd=figures)

    assert proc.returncode == 0, proc.stderr
    assert "mine, newer" in proc.stdout and "mine, older" in proc.stdout
    assert proc.stdout.index("mine, newer") < proc.stdout.index("mine, older"), "newest first"
    assert "someone else's project" not in proc.stdout
    assert "a copy from alice" not in proc.stdout
    scoped = _json.loads(_feedback(desk, "--json", cwd=figures).stdout)
    assert scoped["under"] == str(figures)
    assert [s["source_path"] for s in scoped["sheets"]] == [str(newer), str(mine)]

    everything = _feedback(desk, "--all", cwd=figures).stdout
    for text in ("mine, newer", "mine, older", "someone else's project", "a copy from alice"):
        assert text in everything
    assert "under" not in _json.loads(_feedback(desk, "--all", "--json", cwd=figures).stdout)

    # The directory can be named outright, as present's --in names one.
    proc = _feedback(desk, "--in", str(elsewhere.parent))
    assert "someone else's project" in proc.stdout and "mine" not in proc.stdout


def test_desk_feedback_for_a_path_that_is_not_a_sheet_exits_nonzero(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))

    proc = _feedback(desk, str(fig))

    assert proc.returncode != 0
    assert "is not a sheet on the desk" in proc.stderr
    assert str(fig) in proc.stderr


def test_desk_feedback_never_starts_the_server(free_port, tmp_path):
    proc = run_desk(
        "feedback", port=free_port, data_dir=tmp_path / "never",
        env={"DESK_LOG_DIR": str(tmp_path / "logs")},
    )

    assert proc.returncode != 0
    assert "not running" in proc.stderr
    assert "starting the server" not in proc.stderr
    assert _closed(free_port)
    assert not (tmp_path / "never").exists()


def test_desk_present_says_when_the_sheet_it_presented_has_open_comments(desk, figures):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    sid = desk.publish(fig).json()["sheet"]["id"]

    quiet = run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)
    assert quiet.returncode == 0, quiet.stderr
    assert "open comment" not in quiet.stdout
    assert len(quiet.stdout.strip().splitlines()) == 2

    _comment(desk, "add", sheet_id=sid, anchor=None, text="one")
    _comment(desk, "add", sheet_id=sid, anchor={"x": 0, "y": 0, "w": 0, "h": 0}, text="two")
    loud = run_desk("present", str(fig), port=desk.port, data_dir=desk.data_dir)

    assert loud.returncode == 0, loud.stderr
    lines = loud.stdout.strip().splitlines()
    assert len(lines) == 3
    assert lines[2] == f"2 open comments on this sheet — run: desk feedback {fig}"


def test_desk_present_to_never_mentions_the_recipients_comments(desk, figures, free_port):
    fig = figures / "fit.svg"
    fig.write_text(SVG.format(color="red"))
    first = run_desk("present", str(fig), "--to", f"127.0.0.1:{desk.port}", port=free_port, env=NO_TAILSCALE)
    assert first.returncode == 0, first.stderr
    [sheet] = desk.sheets()
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="theirs, not the sender's")

    again = run_desk("present", str(fig), "--to", f"127.0.0.1:{desk.port}", port=free_port, env=NO_TAILSCALE)

    assert again.returncode == 0, again.stderr
    assert "open comment" not in again.stdout
    assert len(again.stdout.strip().splitlines()) == 2


# --- 20: the feedback button ----------------------------------------------
# The user asks for feedback from the page. The desk holds a waiting agent
# open until then, and tells the page whether anyone was listening.

import threading as _threading  # noqa: E402
from urllib.parse import urlencode as _urlencode  # noqa: E402


class _Waiter:
    """An agent holding `desk feedback --wait` open, as the API sees it."""

    def __init__(self, desk, timeout=10, **scope):
        self.result = None
        query = _urlencode({**scope, "timeout": timeout})
        self.thread = _threading.Thread(
            target=lambda: setattr(self, "result", desk.get("/api/feedback/wait?" + query, timeout=timeout + 10)),
            daemon=True,
        )
        self.thread.start()
        _time.sleep(0.3)  # let it register before anyone presses the button

    def join(self):
        self.thread.join(timeout=20)
        assert self.result is not None, "the wait never answered"
        assert self.result.status == 200, self.result.text
        return self.result.json()["sheet"]


def test_the_feedback_button_wakes_an_agent_waiting_under_that_directory(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="axis labels are too small")
    waiter = _Waiter(desk, under=str(figures))

    pressed = desk.post("/api/feedback", {"sheet_id": sheet["id"]})

    assert pressed.status == 200, pressed.text
    assert pressed.json()["waiters"] == 1
    assert pressed.json()["sheet"]["id"] == sheet["id"]
    woken = waiter.join()
    assert woken["id"] == sheet["id"]
    assert [c["text"] for c in woken["comments"]] == ["axis labels are too small"]
    # Asking changed nothing on the sheet: the comment is still open.
    assert desk.sheets()[0]["open_comments"] == 1


def test_the_button_says_when_nobody_is_waiting(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="one")

    pressed = desk.post("/api/feedback", {"sheet_id": sheet["id"]})

    assert pressed.status == 200, pressed.text
    assert pressed.json()["waiters"] == 0


def test_a_wait_answers_with_no_sheet_once_its_time_is_up(desk, figures):
    waiter = _Waiter(desk, timeout=1, under=str(figures))
    assert waiter.join() is None


def test_a_wait_is_scoped_the_way_desk_feedback_is(desk, figures, tmp_path):
    """A directory wait hears only local sheets under it; a path wait hears
    that sheet; an unscoped wait hears the whole desk."""
    mine, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="mine")
    elsewhere = tmp_path / "other-project" / "old.svg"
    elsewhere.parent.mkdir()
    elsewhere.write_text(SVG.format(color="green"))
    other = desk.publish(elsewhere).json()["sheet"]
    _comment(desk, "add", sheet_id=other["id"], anchor=None, text="someone else's")
    sent = desk.send("alice-mac", ALICE_PATH, SVG.format(color="red").encode()).json()["sheet"]
    _comment(desk, "add", sheet_id=sent["id"], anchor=None, text="a copy from alice")

    under = _Waiter(desk, under=str(figures))
    assert desk.post("/api/feedback", {"sheet_id": other["id"]}).json()["waiters"] == 0
    assert desk.post("/api/feedback", {"sheet_id": sent["id"]}).json()["waiters"] == 0
    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 1
    assert under.join()["id"] == sheet["id"]

    by_path = _Waiter(desk, path=ALICE_PATH)
    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 0
    assert desk.post("/api/feedback", {"sheet_id": sent["id"]}).json()["waiters"] == 1
    assert by_path.join()["id"] == sent["id"]

    everything = _Waiter(desk)
    assert desk.post("/api/feedback", {"sheet_id": other["id"]}).json()["waiters"] == 1
    assert everything.join()["id"] == other["id"]


def test_a_wait_is_answered_once_and_every_waiter_in_scope_hears(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="one")
    first = _Waiter(desk, under=str(figures))
    second = _Waiter(desk, path=str(fig))

    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 2
    assert first.join()["id"] == sheet["id"]
    assert second.join()["id"] == sheet["id"]
    # They were answered, so they are gone: the next press finds nobody.
    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 0


@pytest.mark.parametrize("body", [{}, {"sheet_id": "nope"}, {"sheet_id": 7}])
def test_asking_about_a_sheet_that_is_not_there_is_400(desk, body):
    resp = desk.post("/api/feedback", body)
    assert resp.status == 400
    assert "no live sheet" in resp.json()["error"]


def test_asking_about_a_sheet_with_nothing_open_or_in_the_trash_is_400(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    nothing = desk.post("/api/feedback", {"sheet_id": sheet["id"]})
    assert nothing.status == 400
    assert "no open comments" in nothing.json()["error"]

    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="one")
    desk.post("/api/trash", {"sheet_id": sheet["id"]})
    trashed = desk.post("/api/feedback", {"sheet_id": sheet["id"]})
    assert trashed.status == 400


def test_desk_feedback_wait_reports_the_sheet_the_user_pressed_the_button_on(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor={"x": 0.6, "y": 0.1, "w": 0.3, "h": 0.1}, text="legend overlaps")
    runs = {}
    thread = _threading.Thread(
        target=lambda: runs.setdefault("proc", _feedback(desk, "--wait", "1", cwd=figures)), daemon=True
    )
    thread.start()
    _time.sleep(2.0)  # the command has to reach the desk and start holding

    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 1
    thread.join(timeout=30)

    proc = runs["proc"]
    assert proc.returncode == 0, proc.stderr
    assert str(fig) in proc.stdout
    assert "legend overlaps" in proc.stdout
    assert "fractions: x 0.600–0.900" in proc.stdout


def test_desk_feedback_wait_says_when_nobody_asked_and_exits_zero(desk, figures):
    proc = _feedback(desk, "--wait", "0.02", cwd=figures)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "waited 1.2 seconds and nobody asked for feedback"


def test_desk_feedback_wait_with_a_path_hears_that_sheet_only(desk, figures):
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="mine")
    other_fig, other = _sheet_with_svg(desk, figures, name="hist.svg", color="blue")
    _comment(desk, "add", sheet_id=other["id"], anchor=None, text="not mine")
    runs = {}
    thread = _threading.Thread(
        target=lambda: runs.setdefault("proc", _feedback(desk, "--wait", "1", "--json", str(fig), cwd=figures)),
        daemon=True,
    )
    thread.start()
    _time.sleep(2.0)

    assert desk.post("/api/feedback", {"sheet_id": other["id"]}).json()["waiters"] == 0
    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 1
    thread.join(timeout=30)

    proc = runs["proc"]
    assert proc.returncode == 0, proc.stderr
    report = _json.loads(proc.stdout)
    assert [s["source_path"] for s in report["sheets"]] == [str(fig)]


def test_desk_feedback_wait_ends_loudly_when_the_desk_stops(desk, figures):
    runs = {}
    thread = _threading.Thread(
        target=lambda: runs.setdefault("proc", _feedback(desk, "--wait", "1", cwd=figures)), daemon=True
    )
    thread.start()
    _time.sleep(2.0)

    desk.stop()
    thread.join(timeout=30)

    proc = runs["proc"]
    assert proc.returncode != 0
    assert "stopped while waiting" in proc.stderr
    desk.start()


def test_desk_feedback_wait_then_hands_the_report_to_a_command(desk, figures, tmp_path):
    """A harness that cannot hear a background command finish gets the report
    handed to a command of its own, with the sheet's path in the environment."""
    fig, sheet = _sheet_with_svg(desk, figures)
    _comment(desk, "add", sheet_id=sheet["id"], anchor=None, text="title is wrong")
    landed = tmp_path / "landed.txt"
    then = f"cat > {landed}; echo \"$DESK_SHEET\" >> {landed}"
    runs = {}
    thread = _threading.Thread(
        target=lambda: runs.setdefault("proc", _feedback(desk, "--wait", "1", "--then", then, cwd=figures)),
        daemon=True,
    )
    thread.start()
    _time.sleep(2.0)

    assert desk.post("/api/feedback", {"sheet_id": sheet["id"]}).json()["waiters"] == 1
    thread.join(timeout=30)

    proc = runs["proc"]
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", "the report went to the command, not to stdout"
    text = landed.read_text()
    assert "title is wrong" in text
    assert text.rstrip().endswith(str(fig))

    # Nobody asked: the command does not run, and the one line is printed.
    quiet = _feedback(desk, "--wait", "0.02", "--then", f"echo ran >> {landed}", cwd=figures)
    assert quiet.returncode == 0
    assert "nobody asked" in quiet.stdout
    assert "ran" not in landed.read_text()
