# Diagrammer Help

## Getting Started

Diagrammer is a tool for building diagrams using SVG components. Drag components from the **Component Library** panel onto the canvas, connect their ports with wires, and arrange them into circuit schematics, flowcharts, or general diagrams.

### Basic workflow

1. **Place components** — drag from the library panel, or search by name
2. **Connect ports** — click a port (blue circle) and drag to another port
3. **Route wires** — use trace routing mode (T) to click-place waypoints
4. **Arrange** — move, rotate, flip, align components
5. **Save** — Ctrl+S to save as `.dgm`, or export as SVG/PNG/PDF

---

## Navigation

| Action | How |
|--------|-----|
| Pan | Middle-click drag |
| Zoom at cursor | Scroll wheel |
| Zoom in/out (centered) | Ctrl++ / Ctrl+- |
| Zoom to fit all | **A** |
| Zoom to rectangle | **Z**, then click-drag a rectangle |

---

## Placing Components

- **Drag and drop** from the Component Library panel on the left
- **Search** by typing in the search box at the top of the library
- **Grid view** — click the Grid toggle for a compact thumbnail view
- **Favorites** — right-click a component, select "Add to Favorites"
- **Recently used** — automatically shown at the top of the library

Components snap to the grid by default. The nearest port to your click determines which grid point the component snaps to.

---

## Selecting and Moving

| Action | How |
|--------|-----|
| Select | Click an item |
| Multi-select | Shift+click, or drag a rubber-band rectangle |
| Move | Click and drag a selected item |
| Delete | Delete or Backspace |

When moving a component, the port nearest to your grab point snaps to the grid.

---

## Transforms

| Key | Action |
|-----|--------|
| **Space** | Rotate 90° counter-clockwise |
| **Shift+Space** | Rotate 90° clockwise |
| **R** | Fine rotate 15° CW (around pivot port) |
| **Shift+R** | Fine rotate 15° CCW |
| **F** | Flip horizontal |
| **Shift+F** | Flip vertical |

### Rotation pivot

By default, fine rotation (R key) pivots around the component's first port. To choose a different pivot:

1. **Shift+click** the desired port — it highlights green
2. Press **R** or **Shift+R** to rotate around that port
3. Press **Escape** to clear the pivot

---

## Wiring / Connections

### Quick connect (drag)

1. Hover over a component to reveal its ports (blue circles)
2. Click a port and drag — a rubber-band line appears
3. Drag near another port — it highlights green with a pulse
4. Release to complete the connection

### Trace routing mode (T)

For precise routing with waypoints:

1. Press **T** to enter trace routing mode
2. Click a source port to start
3. Click on empty space to place waypoints (blue dots)
4. Click a target port to complete the connection
5. Right-click or **Escape** to cancel

**Shift** while placing waypoints constrains to horizontal or vertical.

### Editing wires

- **Select** a wire to see waypoint handles (blue squares)
- **Drag** a waypoint handle to move it (snaps to grid)
- **Shift+click** waypoints to multi-select, then drag to move together
- **Double-click** a segment to insert a new waypoint
- **Double-click** a waypoint to delete it
- **Right-click** a waypoint for a context menu with "Delete Waypoint"
- **Drag** a segment to shift it perpendicular (KiCad-style)

### Wire-to-wire junctions

To tap into an existing wire:
- While routing, drag near an existing wire — an orange dot preview appears
- Release (or click in trace mode) to create a junction

### Routing options (Routing menu)

| Option | Description |
|--------|-------------|
| Trace Routing Mode | Click-to-place waypoints |
| Snap to Grid | Waypoints snap to grid |
| Snap to Port | Wire endpoints snap to nearby ports |
| Snap to Angle | Waypoints snap to angle increments |
| Discrete Angle Routing | Route with configurable angle steps (default 15°) |
| Show Junctions | Toggle junction dot visibility |

---

## Layers

Layers let you organize diagram elements and control their visibility and editability.

### Layers panel (right side)

- **Click** a layer to make it active — new items go to this layer
- Items on the active layer **flash briefly** when you switch layers
- **Double-click** a layer to toggle visibility
- **Right-click** a layer for Show/Hide, Lock/Unlock, Rename

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| **H** | Hide active layer |
| **Shift+H** | Show active layer |
| **L** | Lock active layer |
| **Shift+L** | Unlock active layer |

**Hidden layers** — items are invisible but preserved. Wires to hidden items remain in the file.

**Locked layers** — items cannot be selected, moved, or modified. Useful for protecting a finished layer while working on another.

---

## Alignment

Select two or more components, then:

| Shortcut | Action |
|----------|--------|
| **Ctrl+Shift+H** | Align centers horizontally (same Y) |
| **Ctrl+Shift+V** | Align centers vertically (same X) |

### Port-based alignment

For precise port alignment instead of center alignment:

1. **Ctrl+click** specific ports on different components (they turn red)
2. Press **Ctrl+Shift+H** or **Ctrl+Shift+V**
3. The components move so the selected ports line up

---

## Simple Shapes

**Draw menu** provides basic shapes:

- **Rectangle** — resizable with 8 grab handles
- **Ellipse** — resizable
- **Line** — two-point line

**Double-click** any shape to edit its properties (stroke color, width, fill).

---

## Stretchable Components

Some components (e.g., coax cables) can be stretched. When selected, orange diamond handles appear at the edges. Drag a handle to stretch the component while keeping connections intact.

---

## Saving and Exporting

| Action | Shortcut |
|--------|----------|
| New diagram | Ctrl+N |
| Open | Ctrl+O |
| Save | Ctrl+S |
| Save As | Ctrl+Shift+S |
| Export SVG | File > Export as SVG |
| Export PNG | File > Export as PNG |
| Export PDF | File > Export as PDF |

Diagrams are saved as `.dgm` JSON files. All component positions, connections, waypoints, styles, layers, and transforms are preserved.

---

## Settings (Ctrl+,)

### Line Styles
- Default wire width, color, corner radius

### Snap Behavior
- Snap to port, snap to angle, angle increment

### Junctions
- Fill color, outline, radius

### Libraries
- Toggle visibility of component library categories

Settings persist across sessions in `~/.diagrammer/settings.json`.

---

## SVG Component Design

See [svg-component-spec.md](svg-component-spec.md) for the full specification on creating custom SVG components.

Key layers in a component SVG:
- `artwork` — the visual body
- `leads` — connection stems (dynamically shortened for rounded corners)
- `ports` — connection points
- `labels` — text placeholders
- `stretch` — break lines for stretchable components

---

## Tips

- **Grid alignment**: Design SVG ports at multiples of the grid spacing (default 20pt) for clean snapping
- **Stroke matching**: Use 3pt strokes in SVG components to match the default wire width
- **Round caps**: Use `stroke-linecap: round` in SVGs for smooth wire junctions
- **Corner rounding**: The corner radius (Settings > Line Styles) controls how smoothly wires turn. Components with a `leads` layer get automatic lead shortening for seamless rounded corners.
- **Zoom for precision**: Use Z (zoom window) to zoom into detailed areas
