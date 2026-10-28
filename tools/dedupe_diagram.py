"""Collapse stacked duplicates in a .dgm file.

A bug fixed in October 2026 (an undo after an Alt-drag duplicate left
the clones sitting exactly on their originals, and the port auto-join
then wired every coincident copy to every other on the next drag) could
leave a diagram with identical components stacked many deep and
thousands of wires joining them pairwise. This rewrites such a file
with one of each.

Rules, applied to the JSON directly:

* components identical in definition, position, rotation, flips and
  stretch collapse to the first; junctions at the same position likewise;
* wires are re-pointed at the kept item, then a wire whose two ends are
  the same item (a pairwise join between former copies) is dropped, and
  wires with identical endpoints collapse to one;
* annotations identical in text and position collapse to one.

Usage:
    python tools/dedupe_diagram.py IN.dgm OUT.dgm
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def dedupe(data: dict) -> tuple[dict, dict]:
    keep: dict[str, str] = {}  # old id -> surviving id
    stats = {}

    def collapse(items: list, key) -> list:
        first: dict = {}
        out = []
        for it in items:
            k = key(it)
            if k in first:
                keep[it["id"]] = first[k]
            else:
                first[k] = it["id"]
                keep[it["id"]] = it["id"]
                out.append(it)
        return out

    comps = data.get("components", [])
    data["components"] = collapse(comps, lambda c: (
        c.get("def_key"), tuple(c.get("pos", [])), c.get("rotation", 0),
        c.get("flip_h", False), c.get("flip_v", False),
        c.get("stretch_dx", 0), c.get("stretch_dy", 0)))
    stats["components"] = (len(comps), len(data["components"]))

    jcts = data.get("junctions", [])
    data["junctions"] = collapse(jcts, lambda j: tuple(j.get("pos", [])))
    stats["junctions"] = (len(jcts), len(data["junctions"]))

    wires = data.get("connections", [])
    seen = set()
    out = []
    for w in wires:
        s, t = w["source"], w["target"]
        s["component_id"] = keep.get(s["component_id"], s["component_id"])
        t["component_id"] = keep.get(t["component_id"], t["component_id"])
        if s["component_id"] == t["component_id"] and not w.get("closed"):
            continue  # a join between what used to be two stacked copies
        k = frozenset([(s["component_id"], s["port_name"]), (t["component_id"], t["port_name"])])
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    data["connections"] = out
    stats["connections"] = (len(wires), len(out))

    annots = data.get("annotations", [])
    if annots:
        data["annotations"] = collapse(
            annots, lambda a: (a.get("source_text", a.get("text")), tuple(a.get("pos", []))))
        stats["annotations"] = (len(annots), len(data["annotations"]))
    return data, stats


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    data, stats = dedupe(json.loads(src.read_text()))
    dst.write_text(json.dumps(data, indent=2))
    for k, (before, after) in stats.items():
        print(f"  {k:12s} {before:6d} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
