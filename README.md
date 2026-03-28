# Diagrammer

A tool for building diagrams — flowcharts, circuit schematics, and more — using a drag-and-drop interface with user-created SVG components.

Built with PySide6 (Qt for Python).

## Features

- **SVG component library** with drag-and-drop placement, search, favorites, and recently used tracking
- **KiCad-style wire routing** with orthogonal and 45-degree autorouting, waypoint placement, and segment dragging
- **Port-based connections** with snap-to-port, snap-to-grid, and snap-to-angle
- **Component transforms** — rotation (90° and fine 15° increments), flip H/V, stretchable components
- **Rounded wire corners** with dynamic lead shortening for seamless component junctions
- **Wire-to-wire junctions** for T-connections and branches
- **Simple shape drawing** — rectangles, ellipses, lines with editable properties and resize handles
- **Undo/redo** for all operations
- **Cut/copy/paste** with connection topology preserved
- **Zoom** — scroll wheel, Ctrl+/-, zoom window (Z key), zoom all (A key)
- **Grid** — configurable spacing, snap toggle, visual grid with major/minor lines
- **Settings** — persistent style preferences, routing options, library visibility

## Installation

Requires Python 3.10+ and PySide6.

```bash
pip install -e .
```

## Usage

```bash
python -m diagrammer
```

Or if installed:

```bash
diagrammer
```

## Creating Components

Components are standard SVG files with named layers. See [docs/svg-component-spec.md](docs/svg-component-spec.md) for the full specification.

Quick summary — an SVG component has these layers:

| Layer | Purpose |
|-------|---------|
| `artwork` | Component body (rendered) |
| `leads` | Connection stems (dynamically shortened for rounded corners) |
| `ports` | Connection points (`<circle id="port:name" .../>`) |
| `labels` | Text label placeholders |
| `stretch` | Break lines for stretchable components |

Place component SVGs in categorized subdirectories:

```
components/
  electrical/
    resistor.svg
    capacitor.svg
  flowchart/
    process.svg
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| T | Toggle trace routing mode |
| Z | Zoom window mode |
| A | Zoom all / fit |
| Space | Rotate 90° CCW |
| Shift+Space | Rotate 90° CW |
| R | Fine rotate 15° CW (around selected port) |
| Shift+R | Fine rotate 15° CCW |
| F | Flip horizontal |
| Shift+F | Flip vertical |
| H | Align selected horizontally |
| V | Align selected vertically |
| Ctrl+, | Settings |
| Delete/Backspace | Delete selected |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| Ctrl+C/X/V | Copy / Cut / Paste |
| Shift+click component | Multi-select |
| Ctrl+click port | Select port for alignment |
| Shift+click port | Set rotation pivot |
| Middle-click drag | Pan |
| Escape | Cancel current operation |

## License

MIT
