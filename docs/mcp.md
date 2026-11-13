# Driving Diagrammer from Claude (MCP)

Diagrammer ships an [MCP](https://modelcontextprotocol.io) server so Claude can
build a diagram from the component library, look at it, and save a `.dgm` you
then finish by hand in the app. It runs locally over stdio, with no window, and
edits the scene through the same undo commands as the mouse — so the file it
writes is an ordinary diagram.

Status: **prototype.** The API is small and will change.

## Install

```bash
~/miniforge3/envs/CMT/bin/pip install -e ".[mcp]"
```

This adds the `mcp` SDK (1.x and 2.x both work) and a `diagrammer-mcp` command.

## Connect it

**Claude Code** (from any directory):

```bash
claude mcp add diagrammer -- ~/miniforge3/envs/CMT/bin/diagrammer-mcp
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "diagrammer": {
      "command": "/Users/<you>/miniforge3/envs/CMT/bin/diagrammer-mcp"
    }
  }
}
```

Then restart Desktop. The plain claude.ai web chat cannot reach a local server.

## What Claude gets

| Tool | Does |
| ---- | ---- |
| `search_components`, `describe_component` | Find parts; list ports with the direction each lead points |
| `add_component` | Place a part so a chosen port (or its centre) lands on a point |
| `attach_component` | Place a part next to an existing port, turned to face it, and wire them — chains are repeated calls |
| `add_node`, `connect` | T-junctions; wires between existing ports |
| `add_text` | Labels; `$...$` is LaTeX |
| `move`, `remove`, `undo` | Fix layout |
| `get_diagram`, `render` | Inspect as JSON; see it as a PNG |
| `save_diagram`, `export_diagram`, `open_diagram` | `.dgm`; `.pdf`/`.svg`/`.png`; reopen a `.dgm` |

The server's instructions tell Claude the coordinate conventions and to render
and look before it is done. The same operations are a plain Python API in
`diagrammer.headless.Diagram`, for scripts:

```python
from diagrammer.headless import Diagram
d = Diagram()
d.add("PAPER_2pt/circuits/LCR/CAP1", (0, 0), anchor="left", id="C")
d.attach("PAPER_2pt/circuits/gnds/GND1", "left", to=("C", "right"))
d.save("~/Desktop/rc.dgm")
```

## Things that will trip you up

- **Port names are not directions.** In the shipped library a capacitor's
  `left` port points *up*. Tools report a `points` field computed from the
  geometry after rotation and flip; Claude is told to use it.
- **Short ids are not saved.** `J1`, `Cc` live only in the server session; a
  reopened `.dgm` uses its 12-hex instance ids.
- **One diagram per server process.** `new_diagram` discards the current one.

## For developers

Two rules in `mcp_server.py` prevent silent failures — read its docstring
before adding a tool:

1. Every tool is `async`. The SDK runs sync tools in a worker thread, and Qt
   scene/painter objects must stay on the thread that owns the `QApplication`.
2. Every tool is wrapped in `_quiet`: stdout is the protocol channel, so
   prints are routed to stderr, and errors are re-raised as `ToolError` so the
   model sees the message (mcp 2.x otherwise hides it).

Tests: `tests/test_headless.py`.
