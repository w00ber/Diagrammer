"""The headless Diagram API and the MCP server built on it.

Both exist so a caller that cannot see the canvas (a script, or Claude over
MCP) can still build a diagram that opens in the app. The tests pin the parts
of that contract a blind caller depends on: placement is exact and
port-anchored, directions come from geometry not names, every edit is a
single undo step, and the result survives a .dgm round trip.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from diagrammer.headless import Diagram, DiagramError, load_library

P = "PAPER_2pt/circuits/"
CAP = P + "LCR/CAP1"          # ports "top" (points up), "bottom" (down)
JJ = P + "quantum/JJ1"         # ports "top" (points up), "bottom" (down)
GND = P + "gnds/GND1"          # port "top" (points up)
TERM = P + "terminals/terminal_open"  # port at its centre: no direction
COAX = P + "µwave/coax and twp/COAX1_MED"  # 40 wide, gap stretch along dx
TOTAL_PARTS = 238   # shipped tree, decorative parts included


@pytest.fixture(scope="module")
def lib():
    # include_custom=False: a module fixture runs before the settings
    # isolation, so it would otherwise scan the developer's own libraries.
    return load_library(include_overlay=False, include_custom=False)


@pytest.fixture
def d(lib):
    return Diagram(lib)


def _comp(d, cid):
    return next(c for c in d.summary()["components"] if c["id"] == cid)


def _ports(d, cid):
    return _comp(d, cid)["ports"]


def _key(d, tail):
    """The loaded key ending in *tail*. git tracks ``flair/`` but a
    case-insensitive checkout may hold ``FLAIR/``, so never spell the top."""
    hits = [k for k in d.library._by_key if k.lower().endswith(tail.lower())]
    assert len(hits) == 1, hits
    return hits[0]


# -- catalogue ----------------------------------------------------------------

def test_default_library_loads_enabled_custom_folders(tmp_path):
    # Diagram() uses load_library()'s defaults, which must see what the
    # library panel sees: the enabled custom folders, not the disabled ones.
    import shutil
    from diagrammer.headless import builtin_components_root
    from diagrammer.panels.settings_dialog import app_settings
    src = builtin_components_root() / (CAP + ".svg")
    on, off = tmp_path / "on", tmp_path / "off"
    for root, name in ((on, "MINE_ON"), (off, "MINE_OFF")):
        (root / "parts").mkdir(parents=True)
        shutil.copy(src, root / "parts" / f"{name}.svg")
    app_settings.custom_library_paths = [
        {"path": str(on), "enabled": True},
        {"path": str(off), "enabled": False},
    ]
    lib = load_library()
    assert lib.get("parts/MINE_ON").source_kind == "custom"
    assert lib.get("parts/MINE_OFF") is None


def test_overlay_fork_shadows_the_shipped_part():
    # The user's ~/.diagrammer/components/ copy wins over the built-in one
    # with the same key (redirected to a tmp dir by the conftest fixture).
    import shutil
    from diagrammer.headless import builtin_components_root
    from diagrammer.models import overrides
    fork = overrides.default_overlay_root() / (CAP + ".svg")
    fork.parent.mkdir(parents=True)
    shutil.copy(builtin_components_root() / (CAP + ".svg"), fork)
    assert load_library(include_custom=False).get(CAP).source_kind == "overlay"
    assert load_library(include_overlay=False, include_custom=False
                        ).get(CAP).source_kind == "builtin"


def test_search_matches_every_word_and_filters_library(d):
    keys = [r["key"] for r in d.search("cap var", library="PAPER_2pt")["results"]]
    assert keys and all(k.startswith("PAPER_2pt/") for k in keys)
    assert all("cap" in k.lower() and "var" in k.lower() for k in keys)


def test_empty_search_says_it_was_cut_short(d):
    """A capped list that looks complete is how the photon parts got missed."""
    out = d.search("")
    assert out["total"] == TOTAL_PARTS
    assert out["truncated"] and len(out["results"]) == 40
    assert not d.search("", limit=1000)["truncated"]


def test_search_matches_descriptions_not_just_keys(d):
    assert d.search("photon")["total"] == 6
    keys = {r["key"].rsplit("/", 1)[-1] for r in d.search("wavy arrow")["results"]}
    assert keys == {f"PHOTON_{c}{n}" for c in ("BLUE", "RED") for n in "012"}
    assert d.search("propagating", library="flair/photons")["total"] == 6


def test_search_includes_decorative_parts_flagged(d):
    (hit,) = d.search("shaded region")["results"]
    assert hit["decorative"] is True and hit["ports"] == []
    assert hit["stretch_axes"] == ["horizontal", "vertical"]


def test_list_categories_covers_every_part_and_names_roots(d):
    cats = d.list_categories()
    assert cats["total"] == TOTAL_PARTS == sum(cats["categories"].values())
    photons = next(c for c in cats["categories"] if c.lower() == "flair/photons")
    assert cats["categories"][photons] == 6
    assert [r["kind"] for r in cats["roots"]] == ["builtin"]
    assert "not available" in cats["note"]   # the gap is said, not silent


def test_describe_reports_direction_from_geometry_not_name(d):
    ports = {p["name"]: p for p in d.describe(CAP)["ports"]}
    # "points" is computed from where the port sits; the shipped names now
    # agree with it (the validator's port-name-wrong-side keeps them so).
    assert ports["top"]["points"] == "up"
    assert ports["bottom"]["points"] == "down"


def test_describe_reports_stretch_axes_and_tiles(d):
    coax = d.describe(COAX)
    # The raw flag reads backwards: "v" is a vertical *break line*.
    assert coax["stretch"] == {"h": False, "v": True}
    assert coax["stretch_axes"] == ["horizontal"]
    assert d.describe(JJ)["stretch_tile"]
    assert d.describe(CAP)["stretch_axes"] == []


def test_unknown_key_suggests_near_names(d):
    with pytest.raises(DiagramError, match="CAP1"):
        d.add(P + "LCR/CAP_1", (0, 0))


# -- placement ----------------------------------------------------------------

def test_add_puts_anchor_port_exactly_on_point(d):
    d.add(CAP, (100, 60), anchor="bottom", id="C")
    assert _ports(d, "C")["bottom"]["at"] == [100.0, 60.0]


def test_rotation_is_clockwise_on_screen(d):
    d.add(CAP, (0, 0), rotation=90, id="C")
    ports = _ports(d, "C")
    # "top" points up unrotated; a clockwise quarter turn makes it point right.
    assert ports["top"]["points"] == "right"
    assert ports["bottom"]["points"] == "left"


def test_attach_steps_out_turns_to_face_and_wires(d):
    d.add(CAP, (0, 0), anchor="top", id="C")      # "bottom" at (0,100), down
    d.attach(JJ, "bottom", to=("C", "bottom"), gap=40, id="J")
    j = _ports(d, "J")
    assert j["bottom"]["at"] == [pytest.approx(0, abs=0.2), 140.0]
    assert j["bottom"]["points"] == "up"               # faces back at C
    wires = d.summary()["wires"]
    assert [w["from"] + w["to"] for w in wires] == [["C", "bottom", "J", "bottom"]]


def test_attach_to_directionless_port_needs_direction(d):
    d.add(TERM, (0, 0), anchor="left", id="IN")
    with pytest.raises(DiagramError, match="direction"):
        d.attach(CAP, "top", to=("IN", "left"))
    d.attach(CAP, "top", to=("IN", "left"), direction="right", id="C")
    assert _ports(d, "C")["top"]["at"] == [40.0, 0.0]
    assert _ports(d, "C")["top"]["points"] == "left"


def test_wires_take_the_library_weight_even_from_leadless_ends(d):
    """A junction or terminal has no leads, so the command alone would give
    the global default width; a PAPER_2pt diagram must stay at 2 px."""
    d.add(TERM, (0, 0), anchor="left", id="IN")
    d.node((100, 0), id="N")
    d.connect("IN", "left", "N", "center")
    d.attach(GND, "top", to=("N", "center"), direction="down", id="G")
    widths = {round(c.line_width, 2) for c in d.scene.items()
              if type(c).__name__ == "ConnectionItem"}
    assert widths == {2.0}


def test_node_joins_three_wires(d):
    d.node((0, 0), id="N")
    for i, direction in enumerate(("left", "right", "down")):
        d.attach(CAP, "top", to=("N", "center"), direction=direction,
                 id=f"C{i}")
    assert len(d.summary()["wires"]) == 3


# -- stretch ------------------------------------------------------------------

def test_add_with_stretch_lands_anchor_and_is_one_undo_step(d):
    d.add(COAX, (100, 40), anchor="left", stretch=200, id="TL")
    ports = _ports(d, "TL")
    assert ports["left"]["at"] == [100.0, 40.0]
    assert ports["right"]["at"] == [340.0, 40.0]   # 40 wide + 200
    assert _comp(d, "TL")["stretch"] == [200.0, 0.0]
    assert d.undo_stack.count() == 1
    d.undo()
    assert d.summary()["components"] == []


def test_add_stretch_after_flip_and_rotation(d):
    d.add(COAX, (100, 40), anchor="left", stretch=200, flip_h=True,
          rotation=90, id="TL")
    c = _comp(d, "TL")
    assert c["ports"]["left"]["at"] == [100.0, 40.0]
    assert c["stretch_axes"] == ["vertical"]
    (x, y) = c["ports"]["right"]["at"]
    assert x == pytest.approx(100) and abs(y - 40) == pytest.approx(240)


def test_stretch_keeps_port_and_wires_and_undoes(d):
    d.add(CAP, (0, 0), anchor="top", rotation=-90, id="C")   # ports along x
    right = _ports(d, "C")["bottom"]["at"]
    d.add(COAX, tuple(right), anchor="right", flip_h=True, id="TL")
    d.connect("C", "bottom", "TL", "right")
    d.add(GND, (0, 0), id="G")
    d.connect("TL", "left", "G", "top")
    before = _comp(d, "TL")
    n = d.undo_stack.count()

    out = d.stretch("TL", by=(120, 0), keep="right")
    after = _comp(d, "TL")
    assert out == {"stretch": [120.0, 0.0], "kept": "right"}
    assert after["ports"]["right"]["at"] == before["ports"]["right"]["at"]
    assert after["ports"]["left"]["at"] != before["ports"]["left"]["at"]
    for w in (i for i in d.scene.items() if type(i).__name__ == "ConnectionItem"):
        ends = [w._expanded[0], w._expanded[-1]]
        ports = [w.source_port.scene_center(), w.target_port.scene_center()]
        for e, p in zip(ends, ports):
            assert (e.x(), e.y()) == pytest.approx((p.x(), p.y()))
    assert d.undo_stack.count() == n + 1

    d.undo()
    assert _comp(d, "TL")["ports"] == before["ports"]
    assert _comp(d, "TL")["stretch"] == [0.0, 0.0]


def test_stretch_default_keeps_a_port_that_did_not_move(d):
    d.add(COAX, (0, 0), anchor="left", flip_h=True, id="TL")
    at = _ports(d, "TL")
    out = d.stretch("TL", to=80)
    kept = out["kept"]
    assert _ports(d, "TL")[kept]["at"] == at[kept]["at"]


@pytest.mark.parametrize("wired_end", ["left", "right"])
def test_stretch_default_keeps_the_only_wired_end(d, wired_end):
    """One end wired: that end stays, whichever side the break moves."""
    d.add(COAX, (0, 0), anchor="left", id="TL")
    d.node((-200, 0) if wired_end == "left" else (400, 0), id="N")
    d.connect("N", "center", "TL", wired_end)
    at = _ports(d, "TL")[wired_end]["at"]
    out = d.stretch("TL", by=80)
    assert out["kept"] == wired_end
    assert _ports(d, "TL")[wired_end]["at"] == at
    assert _stale_wires(d) == []


def test_stretch_default_with_both_ends_wired_keeps_a_still_port(d):
    d.add(COAX, (0, 0), anchor="left", id="TL")
    d.node((-200, 0), id="A")
    d.node((400, 0), id="B")
    d.connect("A", "center", "TL", "left")
    d.connect("B", "center", "TL", "right")
    before = _ports(d, "TL")
    out = d.stretch("TL", by=80)
    assert _ports(d, "TL")[out["kept"]]["at"] == before[out["kept"]]["at"]
    moved = [n for n in before if _ports(d, "TL")[n]["at"] != before[n]["at"]]
    assert moved and out["kept"] not in moved
    assert _stale_wires(d) == []


def test_flowchart_process_box_stretches_horizontally(d):
    """It declared stretch-h in <metadata> with no break line, so it never
    stretched; the break line now sits at its centre."""
    d.add("flowchart/process", (0, 0), anchor="left", id="P")
    out = d.stretch("P", by=(40, 0), keep="left")
    assert out["stretch"] == [40.0, 0.0]
    ports = _ports(d, "P")
    assert ports["right"]["at"][0] - ports["left"]["at"][0] == pytest.approx(160)


def test_repeat_stretch_snaps_and_reports_what_was_applied(d):
    tile = d.describe(JJ)["stretch_tile"]
    (axis, size), = tile.items()
    want = 2.4 * size
    d.add(JJ, (0, 0), id="J", stretch=want)
    applied = _comp(d, "J")["stretch"]
    i = 0 if axis == "horizontal" else 1
    assert applied[i] != pytest.approx(want)
    assert applied[i] / size == pytest.approx(round(applied[i] / size))
    assert d.stretch("J", by=size)["stretch"] == _comp(d, "J")["stretch"]


def test_stretch_on_unstretchable_part_is_a_clear_error(d):
    with pytest.raises(DiagramError, match="not stretchable"):
        d.add(CAP, (0, 0), stretch=40)
    assert d.undo_stack.count() == 0
    with pytest.raises(DiagramError, match="cannot stretch along dy"):
        d.add(COAX, (0, 0), stretch=(0, 40))


def test_decorative_part_places_by_centre_and_resizes(d):
    key = _key(d, "borders/shaded_region")
    with pytest.raises(DiagramError, match="omit anchor"):
        d.add(key, (0, 0), anchor="left")
    with pytest.raises(DiagramError, match="both axes"):
        d.add(key, (0, 0), stretch=40)
    d.add(key, (0, 0), id="S")
    x0, y0, x1, y1 = _comp(d, "S")["bbox"]
    d.stretch("S", by=(100, 60))
    X0, Y0, X1, Y1 = _comp(d, "S")["bbox"]
    assert (X1 - X0, Y1 - Y0) == pytest.approx((x1 - x0 + 100, y1 - y0 + 60))
    # The lower-case spelling git tracks works on any checkout.
    d.add("flair/borders/shaded_region", (0, 0), id="S2")


# -- undo / move / remove -----------------------------------------------------

def test_one_add_is_one_undo_step(d):
    d.add(CAP, (0, 0), rotation=90, flip_h=True, id="C")
    assert d.undo_stack.count() == 1
    d.undo()
    assert d.summary()["components"] == []


def test_move_carries_wires_and_remove_takes_them(d):
    d.add(CAP, (0, 0), anchor="top", id="C")
    d.attach(GND, "top", to=("C", "bottom"), id="G")
    d.move("C", by=(20, 0))
    assert _ports(d, "C")["top"]["at"] == [20.0, 0.0]
    d.remove("C")
    s = d.summary()
    assert [c["id"] for c in s["components"]] == ["G"]
    assert s["wires"] == []


def _stale_wires(d):
    """Wires whose drawn route does not end on their ports' current spots."""
    from diagrammer.items.connection_item import ConnectionItem
    ids = {v: k for k, v in d._items.items()}
    bad = []
    for item in d.scene.items():
        if not isinstance(item, ConnectionItem):
            continue
        pts = item.all_points()
        ends = (item.source_port.scene_center(), item.target_port.scene_center())
        if (pts[0] - ends[0]).manhattanLength() > 1e-6 or \
                (pts[-1] - ends[1]).manhattanLength() > 1e-6:
            bad.append(ids.get(item, "?"))
    return bad


def _rail(d):
    """The JPA-session repro: a node feeding a cap, down to a node rail
    that ends on a ground."""
    d.node((240, 0), id="A")
    d.add(CAP, (240, 200), anchor="top", id="C")
    d.node((240, 580), id="D")
    d.node((320, 580), id="G")
    d.add(GND, (320, 620), anchor="top", id="GND")
    d.connect("A", "center", "C", "top")
    d.connect("C", "bottom", "D", "center")
    d.connect("D", "center", "G", "center")
    d.connect("G", "center", "GND", "top")
    assert _stale_wires(d) == []


def test_move_by_reroutes_wires_on_nodes_and_parts(d, lib, tmp_path):
    # Regression: the Diagram kept a private undo stack, so the scene's
    # re-route-on-push hook never fired and moved items left their wires
    # drawn where they had been.
    _rail(d)
    for i in ("D", "G", "GND"):
        d.move(i, by=(0, 900))
    assert _stale_wires(d) == []
    d.move("C", by=(0, 500))
    assert _stale_wires(d) == []
    assert _ports(d, "C")["top"]["at"] == [240.0, 700.0]
    # Undo puts the parts back, and the wires with them.
    d.undo()
    assert _ports(d, "C")["top"]["at"] == [240.0, 200.0]
    assert _stale_wires(d) == []
    # The .dgm stores port references, not routes: reopened, it agrees.
    e = Diagram(lib)
    e.load(d.save(tmp_path / "moved"))
    assert len(e.summary()["wires"]) == 4
    assert _stale_wires(e) == []


def test_move_to_reroutes_wires_on_nodes_and_parts(d):
    _rail(d)
    d.move("D", to=(240, 1480))
    d.move("GND", to=(320, 1520), anchor="top")
    d.move("C", to=(240, 700), anchor="top")
    assert _stale_wires(d) == []
    assert _ports(d, "GND")["top"]["at"] == [320.0, 1520.0]


def test_stretch_reroutes_wires(d):
    d.add(COAX, (0, 0), anchor="left", id="TL")
    d.attach(GND, "top", to=("TL", "right"), direction="down", id="G")
    d.node((-40, 0), id="N")
    d.connect("N", "center", "TL", "left")
    d.stretch("TL", by=80, keep="right")      # moves the *left* end
    assert _stale_wires(d) == []
    d.undo()
    assert _stale_wires(d) == []


def test_attach_with_zero_gap_still_connects(d):
    d.add(CAP, (0, 0), anchor="top", id="C")
    d.attach(JJ, "bottom", to=("C", "bottom"), gap=0, id="J")
    assert _ports(d, "J")["bottom"]["at"] == _ports(d, "C")["bottom"]["at"]
    assert len(d.summary()["wires"]) == 1
    d.move("J", by=(0, 40))          # the zero-length wire becomes a real one
    assert _stale_wires(d) == []
    d.attach(JJ, "bottom", to=("J", "top"), gap=0, wire=False, id="J2")
    assert len(d.summary()["wires"]) == 1


@pytest.mark.parametrize("lib_name", ["PAPER_2pt", "SLIDES_3pt"])
@pytest.mark.parametrize("name", ["rfSQUID1", "rfSQUID1_GRAD"])
def test_rf_squid_ports_share_one_axis(d, lib_name, name):
    # A 0.1 px offset between the ports made a stacked column drift.
    ports = d.describe(f"{lib_name}/circuits/quantum/SQUIDs/{name}")["ports"]
    assert len({p["x"] for p in ports}) == 1, ports


def test_duplicate_id_rejected(d):
    d.add(CAP, (0, 0), id="C")
    with pytest.raises(DiagramError, match="already used"):
        d.add(CAP, (0, 0), id="C")


# -- output -------------------------------------------------------------------

def test_render_png_is_a_png(d):
    d.add(CAP, (0, 0))
    assert d.render_png().startswith(b"\x89PNG")


def test_render_empty_is_an_error_not_a_blank_image(d):
    with pytest.raises(DiagramError, match="empty"):
        d.render_png()


def test_dgm_round_trip(d, lib, tmp_path):
    d.add(CAP, (0, 0), anchor="top", id="C")
    d.attach(JJ, "bottom", to=("C", "bottom"), id="J")
    d.text("$E_J$", (60, 120))
    path = d.save(tmp_path / "x")
    assert path.suffix == ".dgm"
    data = json.loads(path.read_text())
    assert {c["def_key"] for c in data["components"]} == {CAP, JJ}

    e = Diagram(lib)
    e.load(path)
    s = e.summary()
    assert len(s["components"]) == 2 and len(s["wires"]) == 1
    assert [t["text"] for t in s["texts"]] == ["$E_J$"]


def test_dgm_round_trip_keeps_stretch(d, lib, tmp_path):
    d.add(COAX, (0, 0), anchor="left", stretch=160, flip_h=True, id="TL")
    ports = _ports(d, "TL")
    e = Diagram(lib)
    e.load(d.save(tmp_path / "s"))
    (c,) = e.summary()["components"]
    assert c["stretch"] == [160.0, 0.0]
    assert {k: v["at"] for k, v in c["ports"].items()} == \
        {k: v["at"] for k, v in ports.items()}


def test_export_formats(d, tmp_path):
    d.add(CAP, (0, 0))
    for suffix in (".svg", ".pdf", ".png"):
        out = d.export(tmp_path / f"x{suffix}")
        assert out.stat().st_size > 0
    with pytest.raises(DiagramError):
        d.export(tmp_path / "x.jpg")


# -- MCP server ---------------------------------------------------------------

@pytest.fixture
def server(lib, monkeypatch):
    srv = pytest.importorskip("diagrammer.mcp_server",
                              reason="mcp SDK not installed")
    monkeypatch.setattr(srv, "_diagram", Diagram(lib))
    return srv


TOOL_NAMES = [
    "search_components", "list_categories", "describe_component",
    "new_diagram", "add_component", "attach_component", "add_node",
    "connect", "add_text", "move", "stretch_component", "remove", "undo",
    "get_diagram", "render", "save_diagram", "export_diagram", "open_diagram",
]


def test_tool_list_is_complete(server):
    """TOOL_NAMES drives the async check; a tool missing from it is unchecked."""
    registered = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert registered == set(TOOL_NAMES)


def test_every_tool_is_async(server):
    """The SDK runs sync tools in a worker thread; Qt objects must stay on
    the thread that owns the QApplication. Async tools run on the loop's."""
    for name in TOOL_NAMES:
        assert inspect.iscoroutinefunction(getattr(server, name)), name


def test_tool_errors_carry_the_message(server):
    asyncio.run(server.add_component(CAP, 0, 0, id="C"))
    with pytest.raises(server.ToolError, match=r"ports are \['top', 'bottom'\]"):
        asyncio.run(server.connect("C", "nope", "C", "top"))
    with pytest.raises(server.ToolError, match="not stretchable"):
        asyncio.run(server.add_component(CAP, 0, 0, stretch=40))
    with pytest.raises(server.ToolError, match="not stretchable"):
        asyncio.run(server.stretch_component("C", by=40))


def test_stretch_and_catalogue_tools(server):
    r = asyncio.run(server.add_component(COAX, 240, 0, anchor="right",
                                         flip_h=True, stretch=320, id="TL"))
    assert r["stretch"] == [320.0, 0.0] and r["stretch_axes"] == ["horizontal"]
    assert r["ports"]["left"]["at"] == [600.0, 0.0]
    r = asyncio.run(server.stretch_component("TL", by=[40, 0], keep="right"))
    assert r["stretch"] == [360.0, 0.0] and r["kept"] == "right"
    assert r["ports"]["right"]["at"] == [240.0, 0.0]
    s = asyncio.run(server.search_components(""))
    assert s["truncated"] and "list_categories" in s["hint"]
    assert asyncio.run(server.list_categories())["total"] == TOTAL_PARTS


def test_tools_never_write_stdout(server, capsys):
    import diagrammer.mcp_server as srv

    @srv._quiet
    async def chatty():
        print("font warning")
        return 1

    assert asyncio.run(chatty()) == 1
    out = capsys.readouterr()
    assert out.out == "" and "font warning" in out.err


def test_chain_through_tools_and_render(server):
    asyncio.run(server.add_component(TERM, 0, 0, anchor="left", id="IN"))
    r = asyncio.run(server.attach_component(CAP, "top", "IN", "left",
                                            direction="right", id="Cc"))
    assert r["ports"]["bottom"]["points"] == "right"
    img = asyncio.run(server.render())
    assert img.data.startswith(b"\x89PNG")
