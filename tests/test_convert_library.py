"""Convert to Library (issue #47, stage 4)."""

from __future__ import annotations

import base64
import json

import pytest

TEE0 = "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE0"
TEE1 = "PAPER_2pt/circuits/µwave/PASSIVE/BIAS_TEE1"
PAPER_CAP = "PAPER_2pt/circuits/LCR/CAP1"


@pytest.fixture
def d(isolate_app_settings):
    from diagrammer.headless import Diagram, load_library
    lib = load_library(include_overlay=False, include_custom=False)
    return Diagram(library=lib)


def _wired_ports(diagram):
    """{library key: sorted port names with a wire on them}, parts only."""
    from diagrammer.items.connection_item import ConnectionItem
    out: dict[str, list] = {}
    for c in diagram.scene.items():
        if isinstance(c, ConnectionItem):
            for p in (c.source_port, c.target_port):
                key = getattr(p.component, "library_key", None)
                if key:
                    out.setdefault(key, []).append(p.port_name)
    return {k: sorted(v) for k, v in out.items()}


# ==========================================================================
# 1. bias-tee port names
# ==========================================================================

def test_bias_tees_use_their_twins_port_names(d):
    for key, names in ((TEE0, {"RF", "COM", "DC"}), (TEE1, {"RF", "COM", "DC"})):
        paper = d.library.get(key)
        slides = d.library.get(key.replace("PAPER_2pt", "SLIDES_3pt"))
        assert {p.name for p in paper.ports} == names
        assert {(p.name, p.x, p.y) for p in paper.ports} == \
            {(p.name, p.x, p.y) for p in slides.ports}


OLD = {TEE0: {"RF": "top", "COM": "bottom", "DC": "dc"},
       TEE1: {"RF": "top", "COM": "bottom", "DC": "left"}}


def _old_file(d, tmp_path, *, embed: bool):
    """A .dgm as it was saved before the rename: old port names on the
    wires, and (with *embed*) the old SVG embedded."""
    d.add(TEE0, (0, 0), id="T0")
    d.add(TEE1, (400, 0), id="T1")
    for i, (tee, ports) in enumerate((("T0", ("RF", "COM", "DC")),
                                      ("T1", ("RF", "COM", "DC")))):
        for j, port in enumerate(ports):
            nid = d.node((i * 400 - 100 + j * 50, 300), id=f"N{i}{j}")
            d.connect(tee, port, nid, "center")
    path = tmp_path / "new.dgm"
    d.save(path)
    data = json.loads(path.read_text())
    keys = {c["id"]: c["def_key"] for c in data["components"]}
    for conn in data["connections"]:
        for end in ("source", "target"):
            key = keys.get(conn[end]["component_id"])
            if key in OLD:
                conn[end]["port_name"] = OLD[key][conn[end]["port_name"]]
    for comp in data["components"]:
        if not embed:
            comp.pop("embedded_svg", None)
            continue
        svg = base64.b64decode(comp["embedded_svg"]).decode()
        for new, old in OLD[comp["def_key"]].items():
            svg = svg.replace(f'id="port:{new}"', f'id="port:{old}"')
        comp["embedded_svg"] = base64.b64encode(svg.encode()).decode()
    old = tmp_path / "old.dgm"
    old.write_text(json.dumps(data))
    return old


@pytest.mark.parametrize("embed", [True, False], ids=["embedded", "bare"])
def test_old_bias_tee_names_migrate_on_load(d, tmp_path, embed):
    from diagrammer.headless import Diagram
    old = _old_file(d, tmp_path, embed=embed)
    text = old.read_text()
    assert '"port_name": "top"' in text and '"port_name": "RF"' not in text
    fresh = Diagram(library=d.library)
    fresh.load(old)
    assert _wired_ports(fresh) == {TEE0: ["COM", "DC", "RF"],
                                   TEE1: ["COM", "DC", "RF"]}
