# SVG Component Specification

This document defines the SVG structure required for Diagrammer components. Components are standard SVG files with specific named layers that Diagrammer uses for rendering, port detection, lead management, and stretchability.

## Units and Scaling

Diagrammer renders SVG components at **1:1 with the viewBox dimensions**. One viewBox unit equals one scene point (pt).

| Concept | Value |
|---------|-------|
| 1 viewBox unit | 1 pt (scene unit) |
| Default grid spacing | 20 pt |
| Default wire stroke width | 3 pt |
| Default corner radius | 8 pt |

**Artboard sizing in Illustrator:** Set your artboard to the exact size you want the component to appear in the diagram. The artboard dimensions become the SVG `viewBox`, which defines the component's display size. There is no automatic scaling.

**Grid alignment tip:** Design port positions at multiples of the grid spacing (20 pt) for clean alignment. For example, place ports at x=0, x=20, x=40, x=60, x=80, x=100, and y values at multiples of 20.

## SVG Root Element

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
```

- The `viewBox` defines the component's coordinate space and display size.
- Do **not** nest all content inside a single wrapper `<g>` (e.g., no `<g id="Layer_1">`). All named layers should be direct children of `<svg>`.

**Illustrator note:** By default, Illustrator wraps all content in a `<g id="Layer_1">`. To avoid this, either:
- Manually edit the exported SVG to move groups to the root level, or
- Use multiple top-level layers in Illustrator named `artwork`, `ports`, `leads`, etc.

## Layer Hierarchy

All layers are `<g>` elements with specific `id` attributes, placed as **direct children of the `<svg>` root**:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">
  <defs>...</defs>

  <g id="artwork">    <!-- visible component body (always rendered) -->
    ...
  </g>

  <g id="leads">      <!-- connection lead lines (shortened at render time) -->
    ...
  </g>

  <g id="ports">      <!-- port markers (hidden, parsed for positions) -->
    ...
  </g>

  <g id="labels">     <!-- label placeholders (hidden, parsed for positions) -->
    ...
  </g>

  <g id="stretch">    <!-- stretch axis definitions (hidden, parsed) -->
    ...
  </g>

  <metadata>           <!-- optional component metadata -->
    ...
  </metadata>
</svg>
```

### Rendering behavior

| Layer | Rendered? | Purpose |
|-------|-----------|---------|
| `artwork` | Yes | The main visual body of the component |
| `leads` | Yes (modified) | Lead/stem lines connecting the body to ports. Shortened at render time to accommodate wire corner rounding. |
| `ports` | No (hidden) | Defines connection port positions |
| `labels` | No (hidden) | Defines label placeholder positions |
| `stretch` | No (hidden) | Defines stretch break axes |
| `decorative` | No (hidden) | Marks component as freely resizable |
| `snap` | No (hidden) | Defines grid snap anchor point |

Diagrammer sets `display="none"` on `ports`, `labels`, `stretch`, `snap`, and `decorative` layers before rendering. The `artwork` and `leads` layers are always rendered, but `leads` elements may be modified (shortened) based on connected wire properties.

## Layer Details

### `artwork` — Component Body

Contains the main visual elements of the component (coil windings, capacitor plates, resistor zigzag, logic gate shape, etc.). This is everything **except** the straight lead/stem lines that connect the body to the ports.

```xml
<g id="artwork">
  <!-- Resistor zigzag -->
  <path d="M21.9,20 l4.7-10.9 9.4,21.9 ..." stroke="#000" stroke-width="3" fill="none"/>
</g>
```

### `leads` — Connection Leads

Contains the straight line segments that connect the component body to the port positions. These are the "stems" or "wires" that extend from the component body to the connection points.

**Why a separate layer?** When a wire connects to a port with corner rounding enabled, Diagrammer shortens the lead line to accommodate the rounded corner. The wire's rounded transition replaces the end of the lead, creating a seamless visual junction.

```xml
<g id="leads">
  <!-- Left lead: horizontal line from port (x=0) to body (x=22) -->
  <path d="M0,29.6 H22.2" stroke="#000" stroke-width="3"
        stroke-linecap="round" fill="none"/>

  <!-- Right lead: horizontal line from body (x=76) to port (x=98) -->
  <path d="M76.2,29.6 H98.4" stroke="#000" stroke-width="3"
        stroke-linecap="round" fill="none"/>
</g>
```

**If no `leads` layer exists:** The component renders entirely from `artwork` with no automatic lead shortening. Wires connect directly to ports with whatever corner style the routing produces. This is fine for components without straight lead stems, or when seamless rounded junctions aren't needed.

**Lead requirements (when using the `leads` layer):**
- Use the same stroke width as the default wire width (3 pt) for seamless joins.
- Use `stroke-linecap="round"` for clean endpoints.
- Each lead should be a simple straight line (horizontal or vertical) from the component body to a port position.
- The lead's endpoint must coincide with the corresponding port's position.

### `ports` — Connection Ports

Defines where wires can connect to the component. Each port is a `<circle>` element with a specific `id` format.

```xml
<g id="ports">
  <circle id="port:left"   cx="0"    cy="29.6" r="3"/>
  <circle id="port:right"  cx="98.4" cy="29.6" r="3"/>
  <circle id="port:top"    cx="50"   cy="0"    r="3"/>
  <circle id="port:bottom" cx="50"   cy="60"   r="3"/>
</g>
```

**Port naming:** `id="port:{name}"` where `{name}` is a descriptive identifier (e.g., `left`, `right`, `top`, `bottom`, `in`, `out`, `gate`, `drain`, `source`).

**Port positioning:** The `cx`/`cy` attributes define the exact connection point in viewBox coordinates. Place ports at the ends of lead lines, typically on the viewBox edges.

**Approach direction:** Diagrammer auto-detects the port's lead direction based on its position relative to the viewBox edges:

| Port position | Detected approach direction |
|--------------|---------------------------|
| `cx` near 0 (left edge) | Wire approaches from left |
| `cx` near viewBox width (right edge) | Wire approaches from right |
| `cy` near 0 (top edge) | Wire approaches from top |
| `cy` near viewBox height (bottom edge) | Wire approaches from bottom |
| Interior position | No preferred direction |

Edge detection tolerance: 2 pt from the viewBox edge.

**Recommendation:** Always define at least one port, even for simple shapes. Ports control snap-to-grid alignment and connection behavior.

### `labels` — Label Placeholders

Defines positions where text labels can be placed on the component.

```xml
<g id="labels">
  <text id="label:value" x="50" y="15"/>
  <text id="label:name"  x="50" y="45"/>
</g>
```

**Label naming:** `id="label:{name}"` where `{name}` identifies the label type.

### `stretch` — Stretch Axes

Defines break lines for stretchable components. A break line indicates where the component can be stretched along a particular axis.

```xml
<g id="stretch">
  <!-- Vertical break line at X=60: component stretches horizontally -->
  <line id="stretch:v" x1="60" y1="0" x2="60" y2="20"/>

  <!-- Horizontal break line at Y=30: component stretches vertically -->
  <line id="stretch:h" x1="0" y1="30" x2="100" y2="30"/>
</g>
```

**How stretching works:**
- A vertical break line (`stretch:v`) at X=B means: everything with X > B shifts right when the component is stretched horizontally.
- A horizontal break line (`stretch:h`) at Y=B means: everything with Y > B shifts down when stretched vertically.
- SVG element coordinates beyond the break line are modified at render time.
- Port positions beyond the break also shift accordingly.
- The SVG content at the break line is extended to fill the gap (vector stretch, not raster).

### `decorative` — Decorative Component Marker

An empty `<g id="decorative"/>` element marks the component as **decorative** — freely resizable in both width and height with no ports or leads. These are intended for visual elements like borders, shaded regions, title blocks, and other non-electrical decorations.

```xml
<g id="decorative"/>
```

Decorative components:

- Show resize handles on all 4 edges when selected (like shape items)
- Scale the entire SVG to fit the current size (no break-line stretching)
- Have no ports, leads, or connection behavior
- Can define a **snap point** for grid alignment (see below)

**Auto-detection:** A component is also treated as decorative if it has no ports and has a snap point defined (even without the explicit `<g id="decorative"/>` marker).

### `snap` — Snap Anchor Point

Defines a single point used for grid snapping when the component is dragged. Most useful for decorative components that have no ports.

```xml
<g id="snap">
  <circle cx="0" cy="0" r="2"/>
</g>
```

The `cx`/`cy` attributes define the snap anchor in viewBox coordinates. Common choices:

- `(0, 0)` — top-left corner (useful for shaded regions placed behind circuits)
- `(width/2, height/2)` — center (default behavior for regular components)

If no snap layer is defined, the component center is used for grid snapping.

### `metadata` — Component Properties

Optional metadata for component behavior.

```xml
<metadata>
  <component
    stretch-h="true"
    stretch-v="false"
    min-width="60"
    min-height="20"/>
</metadata>
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `stretch-h` | boolean | Can be stretched horizontally |
| `stretch-v` | boolean | Can be stretched vertically |
| `min-width` | number | Minimum width when stretched (pt) |
| `min-height` | number | Minimum height when stretched (pt) |

## Style Guidelines

### Stroke widths
- **Lead lines:** 3 pt (matches default wire width for seamless connections)
- **Component body:** 3 pt recommended for consistency; other widths allowed for visual distinction

### Stroke caps and joins
- **Leads:** `stroke-linecap: round` (blends smoothly with wire endpoints)
- **Body:** `stroke-linecap: round; stroke-linejoin: round` recommended

### Colors
- Default: `#000` (black) for strokes
- Components may use custom colors; lead lines should match the wire color for seamless joins (default `#000`)

### Fill
- Most electrical components: `fill: none` (transparent)
- Flowchart shapes: `fill: white` or light colors for solid backgrounds

## Component Library Organization

Components are organized in a directory tree:

```
components/
  electrical/
    resistor.svg
    capacitor.svg
    inductor.svg
    ground.svg
  flowchart/
    process.svg
    decision.svg
    terminal.svg
  custom/
    my_component.svg
```

Each subdirectory becomes a **category** in the library panel. File names (without `.svg`) become the component names displayed in the library.

## Example: Complete Resistor Component

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <defs>
    <style>
      .stroke { fill: none; stroke: #000; stroke-width: 3px;
                stroke-linecap: round; stroke-linejoin: round; }
    </style>
  </defs>

  <g id="artwork">
    <!-- Resistor zigzag body -->
    <path class="stroke"
      d="M22,20 l4.7-10.9 9.4,21.9 9.4-21.9 9.4,21.9 9.4-21.9 9.4,21.9 4.7-10.9"/>
  </g>

  <g id="leads">
    <!-- Left lead: port to body -->
    <path class="stroke" d="M0,20 H22"/>
    <!-- Right lead: body to port -->
    <path class="stroke" d="M78,20 H100"/>
  </g>

  <g id="ports">
    <circle id="port:left"  cx="0"   cy="20" r="3"/>
    <circle id="port:right" cx="100" cy="20" r="3"/>
  </g>

  <g id="labels">
    <text id="label:value" x="50" y="10"/>
  </g>
</svg>
```

## Example: Stretchable Coax Component

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 20">
  <g id="artwork">
    <!-- Connector stubs -->
    <rect x="15" y="4" width="6" height="12" rx="1"
          fill="none" stroke="#000" stroke-width="1.5"/>
    <rect x="99" y="4" width="6" height="12" rx="1"
          fill="none" stroke="#000" stroke-width="1.5"/>
    <!-- Cable body -->
    <line x1="21" y1="10" x2="99" y2="10"
          stroke="#000" stroke-width="3"/>
    <line x1="21" y1="10" x2="99" y2="10"
          stroke="#666" stroke-width="1.5" stroke-dasharray="4,3"/>
  </g>

  <g id="leads">
    <line x1="0" y1="10" x2="15" y2="10"
          stroke="#000" stroke-width="3" stroke-linecap="round"/>
    <line x1="105" y1="10" x2="120" y2="10"
          stroke="#000" stroke-width="3" stroke-linecap="round"/>
  </g>

  <g id="ports">
    <circle id="port:left"  cx="0"   cy="10" r="3"/>
    <circle id="port:right" cx="120" cy="10" r="3"/>
  </g>

  <g id="stretch">
    <line id="stretch:v" x1="60" y1="0" x2="60" y2="20"/>
  </g>

  <metadata>
    <component stretch-h="true" min-width="60"/>
  </metadata>
</svg>
```

## Example: Decorative Shaded Region

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">
  <defs>
    <style>
      .bg { fill: #e8f0ff; stroke: #b0c4de; stroke-width: 1; }
    </style>
  </defs>

  <g id="decorative"/>

  <g id="artwork">
    <rect class="bg" x="0.5" y="0.5" width="119" height="79" rx="6" ry="6"/>
  </g>

  <g id="snap">
    <circle cx="0" cy="0" r="2"/>
  </g>
</svg>
```

This component has no ports or leads. The `<g id="decorative"/>` marker enables free resize on all edges. The snap point at `(0, 0)` means the top-left corner aligns to the grid when dragged.
