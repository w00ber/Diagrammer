"""MCP server: lets Claude build Diagrammer diagrams, look at them, and save them.

Run it as ``diagrammer-mcp`` (or ``python -m diagrammer.mcp_server``) over
stdio. It holds one :class:`diagrammer.headless.Diagram` per process; every
tool is a thin wrapper over that API, so anything done here can also be done
from a plain Python script.

Two rules this module exists to enforce, both silent failures otherwise:

* **Qt stays on one thread.** The MCP SDK (2.x) runs *synchronous* tools in a
  worker thread via ``anyio.to_thread``. Scene and painter objects must not be
  touched off the thread that created the ``QApplication``, and doing so fails
  intermittently rather than loudly. Every tool here is therefore ``async``,
  which the SDK runs on the event-loop thread — the main thread, the same one
  that called :func:`diagrammer.headless.ensure_qapp`.
* **Nothing may print to stdout.** stdout *is* the protocol channel; a stray
  ``print`` or a matplotlib font warning there corrupts the stream. Each tool
  body runs with ``sys.stdout`` redirected to stderr.
"""

from __future__ import annotations

import contextlib
import functools
import json
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.mcpserver.utilities.types import Image
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError

from diagrammer.headless import GRID, Diagram, DiagramError, ensure_qapp

INSTRUCTIONS = f"""\
Diagrammer builds technical diagrams (circuit schematics, microwave and
cryogenic wiring, flowcharts) from a library of SVG components with named
ports. You place parts and wire ports together; the app routes the wires.

Workflow:
1. list_categories FIRST: every folder of the catalogue with counts (cheap).
   Then search_components (words match the key and the part's description)
   and describe_component for ports. A search answer is capped: if it says
   truncated, narrow by library/folder rather than assuming it is all there is.
   Libraries are line-weight sets. Pick ONE per diagram: PAPER_2pt (papers),
   SLIDES_3pt (talks), CRYO_WIRING_6pt (fridge wiring); flowchart/ for
   flowcharts. flair/ holds photon arrows, borders, shaded regions and masks
   for any library — use those parts rather than drawing them with text.
2. Place the first part with add_component, then grow the circuit outward
   with attach_component, which positions a part relative to a port you
   already have, turns it to face that port, and draws the wire.
3. Use add_node for T-junctions (several wires on one point), connect for
   wires between existing ports.
4. render, LOOK at the image, fix overlaps/misalignment with move, render
   again. Then save_diagram (.dgm, editable in the app) and/or
   export_diagram (.pdf/.svg/.png).

Conventions:
- Coordinates are pixels, x right, y DOWN. Grid is {GRID:g} px: keep ports on
  multiples of it so the user can keep editing on the snapping canvas.
- rotation is degrees CLOCKWISE on screen, multiples of 90.
- Port names like top/bottom/left/right describe the UNROTATED part only;
  once a part is rotated or flipped they no longer say which way the port
  faces. Always use the reported "points" field: the way the port's lead
  sticks out of the part on screen, after rotation and flip.
- Ids are yours to choose (J1, Cc, GND1); short ids are auto-assigned
  otherwise. They are not stored in the .dgm file.
- Text supports LaTeX math between $...$.
- Stretchable parts (describe_component -> stretch_axes) are lengthened with
  stretch= on add_component or with stretch_component. Values are [dx, dy]
  in the part's OWN unrotated frame (or one number for a one-axis part) and
  may snap to whole tiles; placed parts report stretch_axes on screen. A
  coax or transmission-line run is ONE stretched part, not chained segments.
  Decorative parts (borders, shaded regions) have no ports: place them by
  centre and size them with stretch=[dx, dy].
"""

mcp = _Server("diagrammer", instructions=INSTRUCTIONS)

_diagram: Diagram | None = None


def _d() -> Diagram:
    global _diagram
    if _diagram is None:
        _diagram = Diagram()
    return _diagram


def _quiet(fn):
    """Run a tool with stdout routed to stderr (stdout is the protocol), and
    hand every failure's text to the model.

    mcp 2.x shows the model only "Error executing tool X" for anything but a
    ``ToolError`` — which would hide exactly the message that says which ports
    or ids *do* exist. This is a local, single-user server, so unexpected
    errors are passed through too: they are the bug report.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            try:
                return await fn(*args, **kwargs)
            except DiagramError as exc:
                raise ToolError(str(exc)) from exc
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    return wrapper


def _brief(cid: str) -> dict:
    """A placed component's id, rotation, stretch and port geometry."""
    for c in _d().summary()["components"]:
        if c["id"] == cid:
            out = {"id": cid, "rotation": c["rotation"], "bbox": c["bbox"],
                   "ports": c["ports"]}
            if c["stretch_axes"]:
                out["stretch"] = c["stretch"]
                out["stretch_axes"] = c["stretch_axes"]
            return out
    return {"id": cid}


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

@mcp.tool()
@_quiet
async def search_components(query: str = "", library: str | None = None,
                            limit: int = 40) -> dict:
    """Find components matching every word of query, in the library key or
    the part's description ("photon", "wavy arrow", "shaded region").

    library is a key prefix: a set such as "PAPER_2pt" or a folder such as
    "flair/photons". Keys look like "PAPER_2pt/circuits/quantum/JJ1".
    The answer carries total and truncated; decorative parts are flagged.
    """
    out = _d().search(query, library, limit)
    if out["truncated"]:
        out["hint"] = (f"showing {len(out['results'])} of {out['total']}: "
                       "use list_categories, a library/folder filter, or "
                       "more words to narrow the search")
    return out


@mcp.tool()
@_quiet
async def list_categories() -> dict:
    """Every folder of the component catalogue with its part count, and which
    library roots are loaded. Call this first when working from a sketch."""
    return _d().list_categories()


@mcp.tool()
@_quiet
async def describe_component(key: str) -> dict:
    """Size and ports of a component as drawn unrotated: each port's position
    in the part's own frame and the direction ("points") its lead sticks out."""
    return _d().describe(key)


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

@mcp.tool()
@_quiet
async def new_diagram() -> str:
    """Discard the current diagram and start an empty one."""
    _d().clear()
    return "empty diagram"


@mcp.tool()
@_quiet
async def add_component(key: str, x: float, y: float,
                        anchor: str | None = None, rotation: float = 0,
                        flip_h: bool = False, flip_v: bool = False,
                        stretch: float | list[float] | None = None,
                        id: str | None = None) -> dict:
    """Place a component so its anchor port (or its centre, if anchor is
    omitted) lands exactly on (x, y). Returns its id and port positions.

    stretch lengthens a stretchable part before it is placed: [dx, dy] in
    its own unrotated frame, or one number for a part with one stretch axis.
    The anchor lands on (x, y) in the stretched geometry."""
    cid = _d().add(key, (x, y), anchor=anchor, rotation=rotation,
                   flip_h=flip_h, flip_v=flip_v, stretch=stretch, id=id)
    return _brief(cid)


@mcp.tool()
@_quiet
async def attach_component(key: str, port: str, to_id: str, to_port: str,
                           gap: float = 2 * GRID,
                           rotation: float | None = None,
                           flip_h: bool = False, flip_v: bool = False,
                           wire: bool = True, direction: str | None = None,
                           id: str | None = None) -> dict:
    """Place a new component next to an existing port and wire them.

    The new part's `port` lands `gap` px out from `to_id.to_port`, along the
    direction that port points. Unless rotation is given, the new part is
    turned so `port` faces back toward `to_port` — so chaining parts in a
    line is just repeated attach calls. `direction` (right/left/up/down)
    overrides the step direction and is required when attaching to a node or
    to a port that has no lead direction. wire=false places without wiring;
    with gap=0 the wire is zero-length (touching ports are not connected
    unless wired).
    """
    cid = _d().attach(key, port, (to_id, to_port), gap=gap, rotation=rotation,
                      flip_h=flip_h, flip_v=flip_v, wire=wire,
                      direction=direction, id=id)
    return _brief(cid)


@mcp.tool()
@_quiet
async def add_node(x: float, y: float, id: str | None = None) -> dict:
    """A junction point with one port, "center", that any number of wires can
    share. Use for T-junctions; the dot appears when 3+ wires meet."""
    nid = _d().node((x, y), id=id)
    return {"id": nid, "port": "center", "at": [x, y]}


@mcp.tool()
@_quiet
async def connect(a: str, a_port: str, b: str, b_port: str,
                  routing: str | None = None) -> dict:
    """Wire port a.a_port to b.b_port. routing: "ortho" (default, H/V only),
    "ortho_45", or "direct" (straight)."""
    wid = _d().connect(a, a_port, b, b_port, routing=routing)
    return {"id": wid, "from": [a, a_port], "to": [b, b_port]}


@mcp.tool()
@_quiet
async def add_text(text: str, x: float, y: float,
                   size: float | None = None, id: str | None = None) -> dict:
    """A text label with its top-left corner at (x, y). $...$ is LaTeX math."""
    tid = _d().text(text, (x, y), size=size, id=id)
    return {"id": tid}


@mcp.tool()
@_quiet
async def move(id: str, x: float | None = None, y: float | None = None,
               dx: float | None = None, dy: float | None = None,
               anchor: str | None = None) -> dict:
    """Move an item. Either to (x, y) — placing its anchor port, or centre —
    or by (dx, dy). Wires attached to it follow."""
    if x is not None and y is not None:
        _d().move(id, to=(x, y), anchor=anchor)
    elif dx is not None or dy is not None:
        _d().move(id, by=(dx or 0.0, dy or 0.0))
    else:
        raise DiagramError("give x and y, or dx and/or dy")
    return _brief(id)


@mcp.tool()
@_quiet
async def stretch_component(id: str, to: float | list[float] | None = None,
                            by: float | list[float] | None = None,
                            keep: str | None = None) -> dict:
    """Lengthen or shorten a placed part; its wires follow.

    to sets the stretch, by adds to it: [dx, dy] in the part's own unrotated
    frame, or one number for a one-axis part. keep is the port that must not
    move (default: one the stretch leaves in place). Returns the applied
    stretch, which may be snapped to whole tiles."""
    applied = _d().stretch(id, to=to, by=by, keep=keep)
    return {**_brief(id), **applied}


@mcp.tool()
@_quiet
async def remove(id: str) -> str:
    """Delete an item; deleting a component also deletes its wires."""
    _d().remove(id)
    return f"removed {id}"


@mcp.tool()
@_quiet
async def undo() -> str:
    """Undo the last edit (an add_component, attach_component or
    stretch_component is one step)."""
    _d().undo()
    return "undone"


# ---------------------------------------------------------------------------
# Inspection and output
# ---------------------------------------------------------------------------

@mcp.tool()
@_quiet
async def get_diagram() -> str:
    """Everything placed so far: components (with port positions and
    directions), nodes, wires and text, as JSON."""
    return json.dumps(_d().summary())


@mcp.tool()
@_quiet
async def render(dpi: int = 96) -> Image:
    """A PNG of the whole diagram. Look at it before declaring the job done."""
    return Image(data=_d().render_png(dpi=dpi), format="png")


@mcp.tool()
@_quiet
async def save_diagram(path: str) -> str:
    """Save as .dgm, which the user can open in Diagrammer and keep editing."""
    return str(_d().save(path))


@mcp.tool()
@_quiet
async def export_diagram(path: str) -> str:
    """Export for publication; the format follows the suffix: .pdf .svg .png"""
    return str(_d().export(path))


@mcp.tool()
@_quiet
async def open_diagram(path: str) -> str:
    """Replace the current diagram with a .dgm file. Ids become the file's
    instance ids; call get_diagram to see them."""
    _d().load(path)
    return json.dumps(_d().summary())


def main() -> None:
    ensure_qapp()   # on the main thread, which also runs the async tools
    mcp.run()


if __name__ == "__main__":
    main()
