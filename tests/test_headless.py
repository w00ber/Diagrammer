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
CAP = P + "LCR/CAP1"          # ports "left" (points up), "right" (down)
JJ = P + "quantum/JJ1"         # ports "bot" (points up), "top" (down)
GND = P + "gnds/GND1"          # port "left" (points up)
TERM = P + "terminals/terminal_open"  # port at its centre: no direction


@pytest.fixture(scope="module")
def lib():
    return load_library(include_overlay=False)


@pytest.fixture
def d(lib):
    return Diagram(lib)


def _ports(d, cid):
    return next(c for c in d.summary()["components"] if c["id"] == cid)["ports"]


# -- catalogue ----------------------------------------------------------------

def test_search_matches_every_word_and_filters_library(d):
    keys = [r["key"] for r in d.search("cap var", library="PAPER_2pt")]
    assert keys and all(k.startswith("PAPER_2pt/") for k in keys)
    assert all("cap" in k.lower() and "var" in k.lower() for k in keys)


def test_describe_reports_direction_from_geometry_not_name(d):
    ports = {p["name"]: p for p in d.describe(CAP)["ports"]}
    # The shipped symbol calls its top port "left". A caller reasoning from
    # the name would wire it wrongly; "points" is what it must use.
    assert ports["left"]["points"] == "up"
    assert ports["right"]["points"] == "down"


def test_unknown_key_suggests_near_names(d):
    with pytest.raises(DiagramError, match="CAP1"):
        d.add(P + "LCR/CAP_1", (0, 0))


# -- placement ----------------------------------------------------------------

def test_add_puts_anchor_port_exactly_on_point(d):
    d.add(CAP, (100, 60), anchor="right", id="C")
    assert _ports(d, "C")["right"]["at"] == [100.0, 60.0]


def test_rotation_is_clockwise_on_screen(d):
    d.add(CAP, (0, 0), rotation=90, id="C")
    ports = _ports(d, "C")
    # "left" points up unrotated; a clockwise quarter turn makes it point right.
    assert ports["left"]["points"] == "right"
    assert ports["right"]["points"] == "left"


def test_attach_steps_out_turns_to_face_and_wires(d):
    d.add(CAP, (0, 0), anchor="left", id="C")      # "right" at (0,100), down
    d.attach(JJ, "top", to=("C", "right"), gap=40, id="J")
    j = _ports(d, "J")
    assert j["top"]["at"] == [pytest.approx(0, abs=0.2), 140.0]
    assert j["top"]["points"] == "up"               # faces back at C
    wires = d.summary()["wires"]
    assert [w["from"] + w["to"] for w in wires] == [["C", "right", "J", "top"]]


def test_attach_to_directionless_port_needs_direction(d):
    d.add(TERM, (0, 0), anchor="left", id="IN")
    with pytest.raises(DiagramError, match="direction"):
        d.attach(CAP, "left", to=("IN", "left"))
    d.attach(CAP, "left", to=("IN", "left"), direction="right", id="C")
    assert _ports(d, "C")["left"]["at"] == [40.0, 0.0]
    assert _ports(d, "C")["left"]["points"] == "left"


def test_wires_take_the_library_weight_even_from_leadless_ends(d):
    """A junction or terminal has no leads, so the command alone would give
    the global default width; a PAPER_2pt diagram must stay at 2 px."""
    d.add(TERM, (0, 0), anchor="left", id="IN")
    d.node((100, 0), id="N")
    d.connect("IN", "left", "N", "center")
    d.attach(GND, "left", to=("N", "center"), direction="down", id="G")
    widths = {round(c.line_width, 2) for c in d.scene.items()
              if type(c).__name__ == "ConnectionItem"}
    assert widths == {2.0}


def test_node_joins_three_wires(d):
    d.node((0, 0), id="N")
    for i, direction in enumerate(("left", "right", "down")):
        d.attach(CAP, "left", to=("N", "center"), direction=direction,
                 id=f"C{i}")
    assert len(d.summary()["wires"]) == 3


# -- undo / move / remove -----------------------------------------------------

def test_one_add_is_one_undo_step(d):
    d.add(CAP, (0, 0), rotation=90, flip_h=True, id="C")
    assert d.undo_stack.count() == 1
    d.undo()
    assert d.summary()["components"] == []


def test_move_carries_wires_and_remove_takes_them(d):
    d.add(CAP, (0, 0), anchor="left", id="C")
    d.attach(GND, "left", to=("C", "right"), id="G")
    d.move("C", by=(20, 0))
    assert _ports(d, "C")["left"]["at"] == [20.0, 0.0]
    d.remove("C")
    s = d.summary()
    assert [c["id"] for c in s["components"]] == ["G"]
    assert s["wires"] == []


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
    d.add(CAP, (0, 0), anchor="left", id="C")
    d.attach(JJ, "top", to=("C", "right"), id="J")
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
    "search_components", "describe_component", "new_diagram",
    "add_component", "attach_component", "add_node", "connect", "add_text",
    "move", "remove", "undo", "get_diagram", "render", "save_diagram",
    "export_diagram", "open_diagram",
]


def test_every_tool_is_async(server):
    """The SDK runs sync tools in a worker thread; Qt objects must stay on
    the thread that owns the QApplication. Async tools run on the loop's."""
    for name in TOOL_NAMES:
        assert inspect.iscoroutinefunction(getattr(server, name)), name


def test_tool_errors_carry_the_message(server):
    asyncio.run(server.add_component(CAP, 0, 0, id="C"))
    with pytest.raises(server.ToolError, match=r"ports are \['left', 'right'\]"):
        asyncio.run(server.connect("C", "nope", "C", "left"))


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
    r = asyncio.run(server.attach_component(CAP, "left", "IN", "left",
                                            direction="right", id="Cc"))
    assert r["ports"]["right"]["points"] == "right"
    img = asyncio.run(server.render())
    assert img.data.startswith(b"\x89PNG")
