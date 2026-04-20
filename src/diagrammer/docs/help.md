# Diagrammer Help

## Getting Started

Diagrammer is a tool for building diagrams using SVG components. Drag components from the **Component Library** panel onto the canvas, connect their ports with wires, and arrange them into circuit schematics, flowcharts, or general diagrams.

### Basic workflow

1. **Place components** — drag from the library panel, or search by name
2. **Connect ports** — click a port (blue circle) and drag to another port
3. **Route wires** — use trace routing mode (W) to click-place waypoints
4. **Arrange** — move, rotate, flip, align components
5. **Save** — Ctrl+S to save as `.dgm`, or export as SVG/PNG/PDF

---

## Navigation

| Action | How |
|--------|-----|
| Pan | Middle-click drag, or two-finger scroll on trackpad |
| Zoom at cursor | Scroll wheel (pinch on trackpad) |
| Zoom in/out (centered) | Ctrl++ / Ctrl+- |
| Zoom to fit all | **A** |
| Zoom to rectangle | **Z**, then click-drag a rectangle (click to zoom in, Shift+click to zoom out) |

---

## Placing Components

- **Drag and drop** from the Component Library panel on the left
- **Search** by typing in the search box at the top of the library
- **Grid view** — click the Grid toggle for a compact thumbnail view
- **List view** — click the List toggle for a tree view with nested categories
- **Favorites** — right-click a component, select "Add to Favorites"
- **Recently used** — automatically shown at the top of the library
- **Show in File Browser** — right-click a component or category to open its directory in Finder/Explorer

Components snap to the grid by default. The nearest port to your click determines which grid point the component snaps to.

---

## Library Table View

View all components in a library category as a reference table:

- **Right-click** a category in the tree view and select **View as Table**
- Click the **grid button** on a category header in grid view
- Click **View All as Table** at the top of the library panel

Table views open in separate tabs. They support zoom (scroll wheel), pan (click-drag), and zoom window (Z key). Export the table view via File > Export as SVG/PNG/PDF while the table tab is active.

- **Ctrl+W** — close the active table tab
- The tab bar is hidden when only the diagram is open

---

## Selecting and Moving

| Action | How |
|--------|-----|
| Select | Click an item |
| Multi-select | Shift+click, or drag a rubber-band rectangle |
| Move | Click and drag a selected item |
| Copy | Ctrl+C |
| Cut | Ctrl+X |
| Paste | Ctrl+V (offset from original) |
| Duplicate | Option/Alt+drag |
| Delete | Delete or Backspace |

When moving a component, the port nearest to your grab point snaps to the grid.

---

## Transforms

| Key | Action |
|-----|--------|
| **Space** | Rotate 90° counter-clockwise |
| **Shift+Space** | Rotate 90° clockwise |
| **R** | Fine rotate 15° CCW (around pivot port) |
| **Shift+R** | Fine rotate 15° CW |
| **F** | Flip horizontal |
| **Shift+F** | Flip vertical |

### Rotation pivot

By default, fine rotation (R key) pivots around the component's first port. To choose a different pivot:

1. **Shift+click** the desired port — it highlights green
2. Press **R** or **Shift+R** to rotate around that port
3. Press **Escape** to clear the pivot

---

## Wiring / Connections

The cursor changes to a **crosshair** when in wiring mode for clear visual feedback.

### Quick connect (drag)

1. Hover over a component to reveal its ports (blue circles)
2. Click a port and drag — a rubber-band line appears
3. Drag near another port — it highlights green with a pulse
4. Release to complete the connection

### Trace routing mode (W)

For precise routing with waypoints:

1. Press **W** to enter trace routing mode (crosshair cursor)
2. Click a source port, wire endpoint, or empty space to start
3. Click on empty space to place waypoints (anchor points)
4. Click a target port to complete the connection
5. **Double-click** on empty space to finish with a free end
6. Right-click or **Escape** to cancel

**Shift** while placing waypoints constrains to horizontal or vertical.

### Starting from existing wires

- Click near an existing wire's **endpoint** to start a new trace from there
- The new segment is automatically joined to the existing wire when finished
- To **extend** a wire: in trace mode, click the wire's endpoint, place new waypoints, then double-click or click a port to finish. The new waypoints are appended to the existing wire.

### Editing wires

- **Select** a wire to see waypoint handles (blue squares)
- **Drag** a waypoint handle to move it (snaps to grid)
- **Shift+click** waypoints to multi-select, then drag any selected handle to move them together
- **Drag-select** (rubber-band) waypoints when a single wire is selected
- **Double-click** a wire segment to select all waypoints for easy whole-wire dragging
- **Ctrl+click** a segment to insert a new waypoint
- **Ctrl+Shift+click** a waypoint to delete it
- **Right-click** a waypoint for a context menu with "Delete Waypoint"

### Free wire endpoints

Wires terminated in empty space (free ends) have draggable anchor points at both ends. Dragging an endpoint anchor moves the wire's end. Free wires can be copied (Ctrl+C/V) and duplicated (Option/Alt+drag).

### Wire joining

- **Automatic**: When routing a new wire to an existing wire's endpoint, the wires are merged automatically with corner rounding at the join
- **Manual (Ctrl+J)**: Select two wires with overlapping endpoints, press **Ctrl+J** to join them into one continuous wire

### Wire-to-wire junctions

To tap into an existing wire:
- While routing, click near an existing wire — a junction is created
- Toggle junction dot visibility in Settings or via the Routing menu

### Routing options (Routing menu)

| Option | Description |
|--------|-------------|
| Trace Routing Mode | Click-to-place waypoints (W key) |
| Snap to Grid | Waypoints snap to grid |
| Snap to Port | Wire endpoints snap to nearby ports |
| Snap to Angle | Waypoints snap to angle increments |
| Discrete Angle Routing | Route with configurable angle steps (default 15°) |
| Show Junctions | Toggle junction dot visibility |

### Wire properties

Select a wire to edit in the Properties panel: width, color, corner radius, and routing mode (Ortho, Ortho+45°, Direct).

---

## Groups

| Shortcut | Action |
|----------|--------|
| **Ctrl+G** | Group selected items |
| **Ctrl+Shift+G** | Ungroup (pops one nesting level) |
| **Ctrl+J** | Join two wires at overlapping endpoints |

Groups support nesting — grouping already-grouped items creates an outer group. Ungrouping removes only the outermost level.

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

## Annotations

Add text annotations to your diagrams:

- **Ctrl+Shift+T** — add a text annotation at the center of the view
- Double-click an annotation to edit its text
- Supports **LaTeX math**: wrap expressions in `$...$` for inline math
- Edit font, size, bold/italic, and color in the Properties panel

---

## Simple Shapes

**Draw menu** provides basic shapes:

- **Rectangle** — resizable with 8 grab handles, adjustable corner radius
- **Ellipse** — resizable
- **Line** — two-point line with optional arrowheads

**Double-click** any shape to edit its properties (stroke color, width, fill, dash pattern, arrowheads).

---

## Stretchable Components

Some components (e.g., coax cables) can be stretched. When selected, orange diamond handles appear at the edges. Drag a handle to stretch the component while keeping connections intact.

---

## Per-Instance Style Overrides

Double-click a component to enter **isolation mode** for editing individual SVG elements:

- The element list shows all artwork and lead elements
- Click an element to flash-highlight it on the canvas
- Modify stroke color, fill, width, opacity, and line cap per element
- Changes are per-instance — other copies of the same component are unaffected

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
| Close Tab | Ctrl+W |

Diagrams are saved as `.dgm` JSON files. All component positions, connections, waypoints, styles, layers, and transforms are preserved.

**Unsaved changes** — a prompt appears when creating a new diagram, opening a file, or quitting with unsaved changes. Choose Save, Discard, or Cancel.

### Export scaling

By default, exports are scaled to **33 %** of the on-screen scene size. This odd scaling has to do with the fact that we are using pts for scaling in the app and wanted to keep the default component sizes at round numbers (e.g. 100 pt strokes, 20 pt grid) for easy SVG authoring. For reference, 1 inch is 72 pt, so a 100 pt stroke becomes ~1.39 inches if exported at 100% scaling. By exporting at 33 %, that same 100 pt stroke becomes ~0.46 inches. For many of the elements that we define in the default libraries, this results in more typical on-paper sizing. As always, if your diagrams are destined for print, you should print out tests at the final scaling to ensure everything looks good at the intended size.

Change the scale in **Settings > Export**. A 100 pt object at 33 % becomes ~33 pt in the exported SVG, PNG, or PDF. The same scale is applied when copying to the clipboard (Ctrl+Shift+C).

Use **Reset to Default** in the Export settings tab to restore the factory default (33 %).

---

## Compound Components

Create reusable components from your diagrams:

1. Select the components, wires, and shapes you want to combine
2. File > **Create Component from Selection**
3. Choose a name and save location
4. The new component appears in the library (after rescan)

Unconnected ports become the new component's ports. Stretched and styled components are baked into the exported SVG.

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
- Add custom library folder paths

### Export

- Export scale (default 33 %) — controls the size of SVG/PNG/PDF exports and clipboard copies relative to the scene

Settings persist across sessions in `~/.diagrammer/settings.json`.

---

## SVG Component Design

See [svg-component-spec.md](svg-component-spec.md) for the full specification on creating custom SVG components.

Key layers in a component SVG:
- `artwork` — the visual body
- `leads` — connection stems (dynamically shortened for rounded corners)
- `ports` — connection points
- `labels` — text placeholders (reserved for future use)
- `stretch` — break lines for stretchable components
- `tile` — repeating stretch content
- `snap` — snap anchor for decorative components
- `decorative` — marks component as freely resizable

---

## Tips

- **Grid alignment**: Design SVG ports at multiples of the grid spacing (default 20pt) for clean snapping
- **Stroke matching**: Use 3pt strokes in SVG components to match the default wire width
- **Round caps**: Use `stroke-linecap: round` in SVGs for smooth wire junctions
- **Corner rounding**: The corner radius (Settings > Line Styles) controls how smoothly wires turn. Components with a `leads` layer get automatic lead shortening for seamless rounded corners.
- **Zoom for precision**: Use Z (zoom window) to zoom into detailed areas
- **Double-click wire to move**: Double-click a wire segment to select all its waypoints, then grab any handle to move the entire wire
- **Wire extension**: In trace mode (W), click a wire endpoint to extend it with new segments
