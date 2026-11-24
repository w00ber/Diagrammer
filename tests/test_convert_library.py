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


# ==========================================================================
# 2. the cross-library twin check
# ==========================================================================

from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
COMPONENTS = REPO / "src" / "diagrammer" / "components"


def test_shipped_twins_agree():
    from collections import Counter

    from diagrammer.svglint.twins import check_twins
    got = check_twins(COMPONENTS)
    assert [t for t in got if t.finding.severity == "error"] == []
    no_twin = Counter(t.path.relative_to(COMPONENTS).parts[0] for t in got
                      if t.finding.check_id == "no-twin")
    assert no_twin == {"PAPER_2pt": 4, "SLIDES_3pt": 1}
    names = sorted(t.path.stem for t in got)
    assert names == ["COAX1_FAT_LCUT", "COAX1_FAT_RCUT", "COAX1_MED_LCUT",
                     "COAX1_MED_RCUT", "COAX_ELBOW1_MED"]


def _pair(tmp_path, paper_rel, slides_rel, edit):
    """Copy a shipped twin pair into a scratch components tree, editing
    the SLIDES file's text with *edit*."""
    src_p = COMPONENTS / "PAPER_2pt" / paper_rel
    src_s = COMPONENTS / "SLIDES_3pt" / slides_rel
    dst_p = tmp_path / "PAPER_2pt" / paper_rel
    dst_s = tmp_path / "SLIDES_3pt" / slides_rel
    for a, b, f in ((src_p, dst_p, lambda t: t), (src_s, dst_s, edit)):
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(f(a.read_text(encoding="utf-8")), encoding="utf-8")
    return dst_p


def test_twin_check_catches_renamed_ports(tmp_path):
    from diagrammer.svglint.twins import check_twins
    tee = "circuits/µwave/PASSIVE/BIAS_TEE0.svg"
    paper = _pair(tmp_path, tee, tee,
                  lambda t: t.replace('id="port:RF"', 'id="port:top"'))
    (f,) = check_twins(tmp_path)
    assert f.finding.check_id == "twin-port-names"
    assert f.finding.severity == "error"
    assert f.path == paper
    assert "RF" in f.finding.message and "top" in f.finding.message


def test_twin_check_catches_moved_ports_across_folder_drift(tmp_path):
    from diagrammer.svglint.twins import check_twins
    # Splitters sit in different folders in the two libraries.
    paper = _pair(tmp_path, "circuits/µwave/splitters/SPLITTER1x2.svg",
                  "circuits/splitters/SPLITTER1x2.svg",
                  lambda t: t.replace('cx="80" cy="30"', 'cx="80" cy="35"'))
    got = check_twins(tmp_path)
    assert [t.finding.check_id for t in got] == ["twin-port-positions"]
    assert got[0].path == paper


def test_find_twin_matches_like_the_library():
    from diagrammer.svglint.twins import find_twin
    others = ["circuits/splitters/SPLITTER1x2", "a/X", "b/X", "c/Lcr/CAP1"]
    assert find_twin("c/LCR/CAP1", others) == "c/Lcr/CAP1"      # folder case
    assert find_twin("circuits/µwave/splitters/SPLITTER1x2", others) == \
        "circuits/splitters/SPLITTER1x2"                          # unique name
    assert find_twin("z/X", others) is None                       # ambiguous


def test_twin_check_runs_without_pyside6():
    import subprocess
    import sys
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'PySide6' or name.startswith('PySide6.'):\n"
        "            raise ImportError('PySide6 is blocked for this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from pathlib import Path\n"
        "from diagrammer.svglint.twins import check_twins\n"
        f"r = check_twins(Path({str(COMPONENTS)!r}))\n"
        "assert 'PySide6' not in sys.modules\n"
        "print(len(r))\n"
    )
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "5"
