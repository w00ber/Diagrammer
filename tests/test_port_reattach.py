"""Saved diagrams keep their wires on the same terminals when a library part's
ports are renamed.

A ``.dgm`` reconnects wires by port name, but every saved component also
embeds the SVG it was drawn with. The loader rebinds a saved name to the
library port at the same position when the two disagree, so renaming
``bot``/``top`` to match where they sit cannot flip a wire to the other end.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = sorted(
    [ROOT / "src/diagrammer/examples/JPA-setup.dgm",
     *(ROOT / "docs/mcp-examples").glob("*.dgm"),
     *(ROOT / "tests/fixtures").glob("*.dgm")]
)


def _embedded_ports(cd: dict) -> dict[str, tuple[float, float]]:
    from diagrammer.models.component_def import ComponentDef

    text = base64.b64decode(cd["embedded_svg"]).decode("utf-8")
    d = ComponentDef.from_svg_text(text, path=Path("x.svg"), infer_stretch=False)
    return {p.name: (p.x, p.y) for p in d.ports}


def _load(path: Path, library):
    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io.serializer import DiagramSerializer
    from diagrammer.items.connection_item import ConnectionItem

    scene = DiagramScene(library=library)
    DiagramSerializer.load(scene, path, library=library)
    conns = {c.instance_id: c for c in scene.items() if isinstance(c, ConnectionItem)}
    return scene, conns


@pytest.mark.parametrize("path", BUNDLED, ids=lambda p: p.name)
def test_bundled_diagrams_keep_every_wire_on_its_saved_terminal(path, library):
    data = json.loads(path.read_text())
    comps = {c["id"]: c for c in data["components"]}
    _, conns = _load(path, library)
    assert len(conns) == len(data["connections"]), "a wire was dropped on load"

    for cd in data["connections"]:
        conn = conns[cd["id"]]
        for end, port in (("source", conn.source_port), ("target", conn.target_port)):
            comp = comps.get(cd[end]["component_id"])
            if comp is None:  # a junction
                continue
            saved = _embedded_ports(comp)[cd[end]["port_name"]]
            here = (port._port_def.x, port._port_def.y)
            assert abs(saved[0] - here[0]) <= 1.0 and abs(saved[1] - here[1]) <= 1.0, (
                f"{comp['def_key']}:{cd[end]['port_name']} was saved at {saved}, "
                f"now bound to '{port.port_name}' at {here}")


def _swap_names(svg: str, a: str, b: str) -> str:
    tmp = "port:__swap__"
    svg = svg.replace(f'"port:{a}"', f'"{tmp}"')
    svg = svg.replace(f'"port:{b}"', f'"port:{a}"')
    return svg.replace(f'"{tmp}"', f'"port:{b}"')


def test_a_swapped_name_follows_the_position_not_the_name(library, tmp_path):
    """Simulate a diagram saved before a part's two port names were swapped."""
    src = ROOT / "docs/mcp-examples/rcsj_chain.dgm"
    data = json.loads(src.read_text())
    jj = next(c for c in data["components"] if c["def_key"].endswith("/JJ1"))
    names = sorted(_embedded_ports(jj))
    assert len(names) == 2
    a, b = names
    svg = base64.b64decode(jj["embedded_svg"]).decode("utf-8")
    jj["embedded_svg"] = base64.b64encode(_swap_names(svg, a, b).encode()).decode()
    for cd in data["connections"]:
        for end in ("source", "target"):
            if cd[end]["component_id"] == jj["id"]:
                cd[end]["port_name"] = {a: b, b: a}[cd[end]["port_name"]]
    path = tmp_path / "swapped.dgm"
    path.write_text(json.dumps(data))

    _, before = _load(src, library)
    _, after = _load(path, library)
    assert before.keys() == after.keys()
    for cid, conn in before.items():
        other = after[cid]
        for p, q in ((conn.source_port, other.source_port),
                     (conn.target_port, other.target_port)):
            assert p.port_name == q.port_name, (
                f"wire {cid} moved from '{p.port_name}' to '{q.port_name}'")


def test_a_port_that_moved_with_nothing_in_its_place_keeps_its_name(library):
    """A deliberate port move must not be mistaken for a rename."""
    from diagrammer.io.serializer import _port_aliases

    key = "PAPER_2pt/circuits/quantum/JJ1"
    comp_def = library.get(key)
    svg = comp_def.svg_path.read_text(encoding="utf-8")
    # The saved copy has every port 20 pt sideways: nothing in the library
    # sits there, so the saved names are trusted as they are.
    moved = re.sub(r'(<circle[^>]*id="port:[^"]*"[^>]*\scx=")([-\d.]+)',
                   lambda m: m.group(1) + str(float(m.group(2)) + 20), svg)
    assert moved != svg
    assert _port_aliases(key, comp_def, base64.b64encode(moved.encode()).decode()) == {}
    # ...and an unmoved saved copy needs no aliases either.
    assert _port_aliases(key, comp_def, base64.b64encode(svg.encode()).decode()) == {}


def test_a_rename_mid_session_is_not_answered_from_the_cache(library):
    """Open, rename the part in the Inspector, reopen: the wire must follow."""
    import copy

    from diagrammer.io.serializer import _port_aliases

    key = "PAPER_2pt/circuits/quantum/JJ1"
    lib_def = library.get(key)
    svg = lib_def.svg_path.read_text(encoding="utf-8")
    saved = base64.b64encode(svg.encode()).decode()
    assert _port_aliases(key, lib_def, saved) == {}

    renamed = copy.copy(lib_def)
    a, b = (p.name for p in lib_def.ports)
    renamed.ports = [copy.copy(p) for p in lib_def.ports]
    renamed.ports[0].name, renamed.ports[1].name = b, a
    assert _port_aliases(key, renamed, saved) == {a: b, b: a}


def test_a_whole_symbol_shift_does_not_move_saved_wires(library, tmp_path):
    """The part snapped onto the grid by its ports when the diagram was drawn.

    Simulate a diagram saved before a whole-symbol drift repair: the saved
    copy has every port 0.3 pt left of where the library now has it. Each
    port must load where the *saved* copy put it, not 0.3 pt off.
    """
    from PySide6.QtCore import QPointF

    from diagrammer.io.serializer import _match_saved_ports

    def with_embed(svg_text: str, name: str) -> tuple[Path, dict]:
        data = json.loads((ROOT / "docs/mcp-examples/rcsj_chain.dgm").read_text())
        jj = next(c for c in data["components"] if c["def_key"].endswith("/JJ1"))
        jj["embedded_svg"] = base64.b64encode(svg_text.encode()).decode()
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return path, jj

    key = "PAPER_2pt/circuits/quantum/JJ1"
    lib_svg = library.get(key).svg_path.read_text(encoding="utf-8")
    shifted = re.sub(r'(<circle[^>]*id="port:[^"]*"[^>]*\scx=")([-\d.]+)',
                     lambda m: m.group(1) + f"{float(m.group(2)) - 0.3:g}", lib_svg)
    ref_path, jj = with_embed(lib_svg, "ref.dgm")
    shf_path, _ = with_embed(shifted, "shifted.dgm")
    assert _match_saved_ports(key, library.get(key), _b64(shifted))[1] == (
        pytest.approx(0.3), pytest.approx(0.0))

    scenes = []  # keep each scene alive while its items are in use

    def item(path):
        scene, _ = _load(path, library)
        scenes.append(scene)
        return next(i for i in scene.items()
                    if getattr(i, "instance_id", None) == jj["id"])

    ref, got = item(ref_path), item(shf_path)
    for p_ref, p_got in zip(ref.ports, got.ports):
        saved_pt = QPointF(p_ref._port_def.x - 0.3, p_ref._port_def.y)
        want = ref.mapToScene(saved_pt)
        have = p_got.scene_center()
        assert (have.x(), have.y()) == (pytest.approx(want.x(), abs=1e-6),
                                        pytest.approx(want.y(), abs=1e-6))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_a_compound_follows_the_position_too(library):
    """``.dgmcomp`` manifests embed the SVG through the same helper."""
    from PySide6.QtCore import QPointF

    from diagrammer.canvas.scene import DiagramScene
    from diagrammer.io.compound_manifest import instantiate_compound
    from diagrammer.items.connection_item import ConnectionItem

    def place(manifest):
        scene = DiagramScene(library=library)
        items = instantiate_compound(scene, manifest, QPointF(0, 0), library)
        wires = sorted(
            ((i.source_port.component.component_def.name if hasattr(i.source_port.component, "component_def") else "j",
              i.source_port.port_name,
              i.target_port.component.component_def.name if hasattr(i.target_port.component, "component_def") else "j",
              i.target_port.port_name)
             for i in items if isinstance(i, ConnectionItem)))
        return scene, wires

    data = json.loads((ROOT / "docs/mcp-examples/rcsj_chain.dgm").read_text())
    manifest = {k: data[k] for k in ("components", "junctions", "connections")}
    _s1, plain = place(json.loads(json.dumps(manifest)))

    jj = next(c for c in manifest["components"] if c["def_key"].endswith("/JJ1"))
    a, b = sorted(_embedded_ports(jj))
    svg = base64.b64decode(jj["embedded_svg"]).decode("utf-8")
    jj["embedded_svg"] = _b64(_swap_names(svg, a, b))
    for cd in manifest["connections"]:
        for end in ("source", "target"):
            if cd[end]["component_id"] == jj["id"]:
                cd[end]["port_name"] = {a: b, b: a}[cd[end]["port_name"]]
    _s2, swapped = place(manifest)
    assert len(plain) == len(data["connections"])
    assert swapped == plain
