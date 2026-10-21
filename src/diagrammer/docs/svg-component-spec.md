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

> ⚠️ **Export with "Use Artboards" — the `viewBox` must equal the artboard, not the artwork.**
>
> When you **Save As / Export → SVG**, make sure the **Use Artboards** option is checked (Export → Export As… → SVG shows it directly; it scopes the `viewBox` to the active artboard). Without it — and this became the *default behaviour in some Illustrator 30.6+ builds* — the exporter sizes the `viewBox` to the **bounding box of all artwork** instead of the artboard. Two things routinely sit *outside* the artboard and then inflate that box:
>
> - **Guide/grid layers** drawn past the artboard edges (e.g. a 120-wide alignment grid around a 40-wide component).
> - **Edge ports:** a `port` circle centred on an edge (`cx=0`, `cy=0`, …) extends half its radius past the artboard, and edge artwork extends half a stroke width past it.
>
> The exporter also **re-bases** coordinates so nothing is negative, shifting the whole component inward. The symptoms:
>
> - The component renders with a **much larger bounding box** than the artboard would suggest.
> - **Ports no longer land on the `viewBox` edges**, so edge/approach detection (2 pt tolerance) fails and wires attach from the wrong direction.
> - **Stretch misbehaves** — the break line and ports are in the wrong place relative to the inflated frame.
>
> **How to catch it:** after export, confirm the `viewBox` numbers equal your artboard size. If the width/height are larger (often by the port radius, ~3 pt per edge, or by a whole guide grid), re-export with **Use Artboards** on. The generator comment near the top of the SVG records the Illustrator version, which helps when a tool upgrade silently changes the default.

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
  <!-- Or, for stretchable components, split leads by direction
       (preferred — each group rides as a unit with its port): -->
  <!-- <g id="leads-left">...</g> <g id="leads-right">...</g>      -->
  <!-- <g id="leads-top">...</g>  <g id="leads-bottom">...</g>     -->

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
| `leads` | Yes (modified) | Lead/stem lines connecting the body to ports. Shortened at render time to accommodate wire corner rounding. Per-coordinate stretch shifting. |
| `leads-left` / `leads-right` / `leads-top` / `leads-bottom` | Yes (modified) | Same as `leads` but tagged by direction. Each group translates as a single unit when stretched (preferred for stretchable components). |
| `ports` | No (hidden) | Defines connection port positions |
| `labels` | No (hidden) | Defines label placeholder positions |
| `stretch` | No (hidden) | Optional explicit stretch break axes. When absent, axes are inferred from `tile` and direction-tagged leads. |
| `tile` | Yes (cloned) | Tile unit for repeating stretch (inside artwork) |
| `upright` | Yes (compensated) | Baked lettering that stays readable when the component is flipped or rotated (inside artwork) |
| `decorative` | No (hidden) | Marks component as freely resizable |
| `snap` | No (hidden) | Defines grid snap anchor point |

Diagrammer sets `display="none"` on `ports`, `labels`, `stretch`, `snap`, and `decorative` layers before rendering. The `artwork` and `leads` layers are always rendered, but `leads` elements may be modified (shortened) based on connected wire properties.

![Layer anatomy: exploded view showing artwork, leads, ports, and labels layers separated vertically with rendering status annotations](fig-layer-anatomy.svg)

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

![Lead shortening: comparison showing sharp corner without shortening vs seamless rounded junction with lead shortened by corner radius](fig-lead-shortening.svg)

```xml
<g id="leads">
  <!-- Left lead: horizontal line from port (x=0) to body (x=22) -->
  <path d="M0,29.6 H22.2" stroke="#000" stroke-width="3"
        stroke-linecap="butt" fill="none"/>

  <!-- Right lead: horizontal line from body (x=76) to port (x=98) -->
  <path d="M76.2,29.6 H98.4" stroke="#000" stroke-width="3"
        stroke-linecap="butt" fill="none"/>
</g>
```

**If no `leads` layer exists:** The component renders entirely from `artwork` with no automatic lead shortening. Wires connect directly to ports with whatever corner style the routing produces. This is fine for components without straight lead stems, or when seamless rounded junctions aren't needed.

**Lead requirements (when using the `leads` layer):**
- Use the host library's lead weight so wires join without a step in thickness —
  3 pt matches the default wire width, but see
  [Line weight is a property of the library](#line-weight-is-a-property-of-the-library).
- Use `stroke-linecap="butt"` so the lead ends flush at its port; `round`
  and `square` overhang by half the stroke width (see Style Guidelines).
- Each lead should be a simple straight line (horizontal or vertical) from the component body to a port position.
- The lead's endpoint must coincide with the corresponding port's position.

#### Direction-tagged leads (preferred for stretchable components)

Instead of one `leads` group, split leads by the port they serve:

| Group ID       | Behavior under stretch                                       |
| -------------- | ------------------------------------------------------------ |
| `leads-left`   | Stays anchored to the left edge — does not move              |
| `leads-right`  | Translates by the horizontal stretch amount as a single unit |
| `leads-top`    | Stays anchored to the top edge — does not move               |
| `leads-bottom` | Translates by the vertical stretch amount as a single unit   |

Why prefer this over the legacy `leads` group? With direction-tagged groups Diagrammer applies a single `transform="translate(dx,0)"` (or `translate(0,dy)`) to the whole group, so every path, polygon, and shape inside rides together — its intent is unambiguous. The legacy `leads` group (and `artwork`) is shifted **per coordinate**. Diagrammer normalizes each path to absolute coordinates before shifting, so an Adobe-exported path (one absolute moveto + relative segments) is handled correctly rather than sliding apart — but the *per-coordinate* rule still has to infer, from the geometry alone, whether a subpath should stretch across the break or ride past it as a unit. Direction-tagged groups state that intent explicitly, so reach for them whenever a lead's role could be ambiguous.

```xml
<g id="leads-left">
  <!-- Left-side leads stay put when the component stretches horizontally -->
  <path d="M0,10 H17" stroke="#000" stroke-width="3"
        stroke-linecap="butt" fill="none"/>
</g>

<g id="leads-right">
  <!-- Right-side leads + arrowhead translate together with the right port -->
  <path d="M27,10 c…"
        stroke="#000" stroke-width="3" stroke-linecap="butt" fill="none"/>
  <polygon points="53,12 60,10 53,8" fill="#000"/>
</g>
```

You can mix direction-tagged groups with a legacy `leads` group; the legacy group still uses per-coordinate shifting. Direction-tagged groups also receive lead shortening, style overrides, and id/class prefixing on compound export — no other authoring changes required.

![Direction-tagged leads: before/after stretch comparison showing leads-right group translating as a single unit, with side-by-side illustration of the legacy `leads` group's coordinate-rounding failure mode where a lead path detaches from its arrowhead](fig-direction-tagged-leads.svg)

**Avoiding visible "comb teeth" on smooth strokes.** When a path is built from many short cubic-Bézier segments (typical of Adobe-Illustrator wave / oscillator artwork), each segment-to-segment join with the SVG default `stroke-linejoin: miter` produces a small spike. Across dozens of segments these spikes look like a regular jagged pattern. Set `stroke-linejoin: round` on the relevant CSS class (or attribute) to absorb those joins:

```css
.st1 {
  stroke: #0e72ba;
  stroke-linecap: butt;
  stroke-linejoin: round;   /* avoids miter-spike artifacts */
  stroke-width: 1.7px;
}
```

**Adobe Illustrator export precision.** When you author or paste a smooth path in Illustrator and then **Save As → SVG**, the export's *Decimal Places* setting controls how aggressively coordinates are rounded. The default is **1**, which is far too coarse for typical component viewBoxes — a control point at `(17.4523, 13.9217)` rounds to `(17.5, 13.9)`, and across a smooth multi-segment cubic those rounding errors break G1 continuity at the joints, producing visibly kinked curves. (You can spot this by reopening the saved SVG in Illustrator: the curves there will look just as jagged as in Diagrammer — the precision is already lost in the file.)

In the **SVG Options** dialog, expand **More Options** and set:

- **Decimal Places: 3** (or **4** if your viewBox is unusually small).
- **CSS Properties: Style Elements** — emits a single `<style>` block with `.st0`/`.st1`/… class rules, matching the convention of the bundled components. ("Style Attributes" inlines `style="…"` on every element, which works but defeats the per-class style-override system Diagrammer uses.)

A complementary practice: author at a **larger working viewBox** (e.g., `0 0 600 200` instead of `0 0 60 20`). Adobe's interactive tools are less twitchy at larger scales, and any residual rounding error becomes proportionally smaller. The on-canvas rendered size in Diagrammer is unaffected — viewBox sets the internal coordinate system, not the placed size.

Don't open and re-save an already-degraded SVG to "fix" it — each Save round-trip compounds the precision loss. If a component looks wrong, re-export from the original Illustrator source with higher Decimal Places.

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

![Port direction detection: viewBox with edge ports showing approach arrows, interior port with no preferred direction, and 2pt tolerance zones highlighted](fig-port-direction.svg)

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

### `stretch` — Stretch Axes (optional override)

Defines break lines for stretchable components. A break line indicates where the component can be stretched along a particular axis.

```xml
<g id="stretch">
  <!-- Vertical break line at X=60: component stretches horizontally -->
  <line id="stretch:v" x1="60" y1="0" x2="60" y2="20"/>

  <!-- Horizontal break line at Y=30: component stretches vertically -->
  <line id="stretch:h" x1="0" y1="30" x2="100" y2="30"/>
</g>
```

#### When you can omit the stretch layer

For each axis, Diagrammer infers stretchability from the new layer convention if no `<g id="stretch">` declaration exists for that axis:

1. **Repeat stretch** is inferred from a `<g id="tile">` group inside `<g id="artwork">`. The tile element's bbox along the stretch axis (X for horizontal, Y for vertical) becomes the repeat region. The axis is chosen by which direction-tagged lead groups are present (`leads-left`/`leads-right` → horizontal, `leads-top`/`leads-bottom` → vertical). With no directional leads at all, a tile is assumed to be horizontal.
2. **Anchored gap stretch** is inferred from direction-tagged lead groups when no tile is present. The break line sits between the inner edges of the two groups (`leads-left.right` ↔ `leads-right.left`, or top/bottom equivalents) so the moving lead group translates while everything else stays put.
3. The **explicit stretch layer takes precedence per-axis**: if you declare `stretch:v` but not `stretch:h`, Diagrammer uses your X-axis declaration and infers Y from the layer convention.

When to keep an explicit `<g id="stretch">`:

- Your tile element extends visually past its repeat region (e.g., overlapping rounded caps), so the bbox-based inference would be too wide. Declare `stretch:v1`/`v2` precisely.
- You want gap stretch on a component with no direction-tagged leads (legacy `<g id="leads">` only).
- Your component has artwork past the tile region that should stay anchored differently than what the inferred break line would do.

**Gap stretch (single break line):**
- A vertical break line (`stretch:v`) at X=B means: everything with X > B shifts right when the component is stretched horizontally.
- A horizontal break line (`stretch:h`) at Y=B means: everything with Y > B shifts down when stretched vertically.
- SVG element coordinates beyond the break line are modified at render time.
- Port positions beyond the break also shift accordingly.
- The SVG content at the break line is extended to fill the gap (vector stretch, not raster).

> **Tip:** Direction-tagged lead groups (`leads-right`, `leads-bottom`) translate as a unit by the stretch amount, regardless of where their inner coordinates fall relative to the break — the clearest option when a lead should ride past the break intact. The break line still applies per-coordinate to the legacy `leads` group and to `artwork`; Diagrammer normalizes those paths to absolute coordinates first, so Adobe-style `M` + relative-curves authoring is handled correctly (a subpath that straddles the break stretches; one lying entirely past it translates as a unit). Direction-tagged groups are still preferable when you want that intent stated explicitly rather than inferred.

**Repeating stretch (two break lines):**

Defines a tile region between two break lines. When stretched, the content between the breaks is repeated (tiled) to fill the new length. Content outside the region stays intact; content beyond the second break shifts.

```xml
<g id="stretch">
  <!-- Horizontal repeating: tile content between X=20 and X=40 -->
  <line id="stretch:v1" x1="20" y1="0" x2="20" y2="20"/>
  <line id="stretch:v2" x1="40" y1="0" x2="40" y2="20"/>

  <!-- Vertical repeating: tile content between Y=10 and Y=30 -->
  <line id="stretch:h1" x1="0" y1="10" x2="100" y2="10"/>
  <line id="stretch:h2" x1="0" y1="30" x2="100" y2="30"/>
</g>
```

- The stretch amount snaps to integer multiples of the tile width (break2 - break1)
- **Design tip:** Make the tile width an integer multiple of the grid spacing (20pt) so ports still align to the grid after stretching
- Elements beyond the second break line shift by the total growth amount
- **Tile content must be in a `<g id="tile">` group** within the artwork layer. Only this group is cloned and repeated. If no tile group is found, the entire artwork is cloned with clipPath clipping (less reliable).

```xml
<g id="artwork">
  <!-- Left end cap (not repeated) -->
  <path d="..."/>

  <!-- Tile unit: this group is cloned and repeated -->
  <g id="tile">
    <path d="..."/>  <!-- one period of the repeating pattern -->
  </g>

  <!-- Right end cap (not repeated, shifts right) -->
  <path d="..."/>
</g>
```

**Important:** The tile group should contain ONLY the geometry between the two break lines. Paths should not extend beyond the break boundaries.

![Stretch mechanics: gap stretch vs repeating stretch, showing before and after states with break lines, gap fill regions, and cloned tiles](fig-stretch-mechanics.svg)

### `upright` — Lettering That Stays Readable

A component's transform is `translate(c) * scale(flip) * rotate(angle) *
translate(-c)`, applied to everything the item draws. Lettering baked into
`artwork` therefore comes out mirrored on a flipped part and lying on its side
on a rotated one.

Put such lettering in a `<g id="upright">` **inside** `artwork`, and give each
child a `data-anchor="x,y"` — the point it should stay pinned to:

```xml
<g id="artwork">
  <circle class="st1" cx="60" cy="60" r="24"/>
  <g id="upright">
    <path class="st3" data-anchor="18,54" transform="translate(10.3,54) scale(1,-1)"
          d="M4.5,4.3 …"/>
  </g>
</g>
```

At render time `ComponentItem._apply_upright` prepends
`translate(p) * M⁻¹ * translate(-p)` to each child, where `M` is the linear part
of the item transform. That cancels the rotation and the mirroring for the glyph
while leaving its anchor wherever the item transform puts it — so the text stays
readable *and* still travels with the port it names. Compensation is per child,
not per group, which is what makes a label follow its own port under rotation.

**Put the `upright` group at the top level, after `leads`** — not inside
`artwork`. SVG paints in document order, so lettering inside `artwork` is drawn
*before* the leads and a lead will simply paint over it.

**Two things keep a label clear of the wiring**, and they solve different halves
of the problem:

- *Clearance* handles the component's own leads. Size the perpendicular offset
  by the glyph's largest reach from its anchor, not its height: the label stays
  upright while its offset vector rotates, so after a quarter turn the glyph
  presents a different edge to the lead. `"LO"` is nearly twice as wide as it is
  tall, and a flat offset that suits `"I"` leaves it overlapping.
- *A white knockout box* handles everything else. A wire the user routes past
  the part can cross a label wherever it is placed, so put a white `<rect>`
  covering the glyph's bounding box underneath it.

  Make it a rectangle, **not** a white-stroked copy of the glyph. A stroked halo
  follows the letterforms, so a wire grazing its edge comes away scalloped and
  reads as a damaged conductor; a rectangle either cuts the wire cleanly or
  leaves it alone. Get the clearance right as well, or the knockout will punch a
  gap in the component's own lead.

This is for lettering that is part of the drawing. It is not the `labels` layer,
which is a separate (currently unimplemented) hook for editable placeholders.

> The `_LBL` filter components predate this and bake their lettering directly
> into `artwork`, so they still mirror when flipped. Moving them into an
> `upright` group is a drop-in fix whenever they are next touched.

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
- **Lead lines:** match the host library's lead weight — see
  [Line weight is a property of the library](#line-weight-is-a-property-of-the-library)
  below. 3 pt is the default wire width and the right answer for a standalone
  component, but a part dropped into `PAPER_2pt` must use 2 pt or its wires will
  step in thickness at the port.
- **Component body:** not standardised — see
  [Body weight is per-family](#body-weight-is-per-family).

### Stroke caps and joins
- **Caps:** `stroke-linecap: butt` — a flush end. Both `round` and `square`
  overhang the endpoint by half the stroke width, and that overhang shows:
  it pokes out past a tee-junction line join, and past the body outline
  wherever an interior line runs up to it.
- **Joins:** `stroke-linejoin: round`.

> **Butt caps change how you draw a multi-segment run.** A right-angled branch
> drawn as separate `<line>` elements relied on round caps to fill its corners;
> with butt caps each corner notches by a quarter of the stroke width. Draw the
> whole run as one `<path>` (`M … H … V … H …`) so the corners are *joins*
> rather than free ends. `tools/gen_splitters.py`'s square cascade does this.

Much of the existing library still declares `stroke-linecap: round` (127 of 229
files; the other 102 declare nothing and so already get `butt`). New components
should use `butt`; the older files are worth converting whenever they are next
touched.

### Colors
- Default: `#000` (black) for strokes
- Components may use custom colors; lead lines should match the wire color for seamless joins (default `#000`)

### Fill
- Most electrical components: `fill: none` (transparent)
- Flowchart shapes: `fill: white` or light colors for solid backgrounds

## House Conventions (the shipped libraries)

Everything above is the contract the *application* enforces: get it wrong and
ports go undetected or leads fail to shorten. This section is different — it is
what the bundled libraries happen to agree on. Nothing here is checked by code,
but a part that ignores it looks wrong sitting next to its neighbours.

### Line weight is a property of the library

The same component is shipped once per library at a different line weight, so a
diagram drawn for a paper and the same diagram drawn for slides both read
correctly at their final size. **A new part belongs in every library it is
relevant to, drawn at that library's weight** — not in one library only.

| Library | Lead weight | Intended for |
| ------- | ----------- | ------------ |
| `PAPER_2pt` | 2 px | figures in papers |
| `SLIDES_3pt` | 3 px | presentation slides |
| `CRYO_WIRING_6pt` | 6 px | large-format fridge wiring diagrams |
| `FLAIR` | n/a | illustrative/decorative parts, no fixed weight |
| `flowchart` | n/a | flowchart shapes, no leads |

The lead weight is the one that must be exact. `ConnectCommand._get_lead_stroke_width`
parses it out of the component's CSS and gives the attached wire the same width,
so a mismatch shows up as a visible step in thickness at the port.

### Body weight is per-family

Body weight is *not* standardised, and the libraries are not self-consistent
about it — bodies in `PAPER_2pt` range from 1.6 px to 3.2 px, and even within the
switch family `SP6T` uses a 2.7 px body while `SPDT` uses 2 px.

Nor is there a single rule about body versus interior. The families do opposite
things, deliberately:

- In `switches`, the body is the heaviest line (`SP6T`: 2.7 px box, 2 px
  contacts and arms) so the outline reads above its contents.
- In `PASSIVE`, the *signal path* is the heaviest line and the box recedes
  (`90degHYBRID0`, `DIR_COUPLER1`: 4 px transmission lines, 2 px box,
  1.6 px flanges) so the coupling topology is what you see first.

When adding to an existing family, **open the specific neighbour you want to sit
beside, read its body weight, and match that** rather than reaching for a
nominal value. Record which part you matched in the generator or a comment, so
the next person does not have to re-derive it.

### Port grids in the circuit libraries

The `circuits/` families share a footprint so that parts are interchangeable on
the canvas — a splitter can replace a switch without moving a wire:

- viewBox 80 pt wide, with the common/input port at `x=0` and outputs at `x=80`
- ports on a 20 pt pitch (40 pt when there are only two, matching `SPDT`)
- the common port centred vertically: `cy = height / 2`
- body inset 10 pt from the viewBox on all sides, `rx="4"` on the rounded rect
- leads spanning the inset only (`x=0→10` and `x=80→70`), so the lead/body weight
  change lands exactly on the outline rather than somewhere inside the body

### Generate families, do not hand-author them

A part that exists at several N, across several libraries, is a cross product —
eleven symbols times two libraries for the splitters. Hand-authoring those
drifts. Write a generator under `tools/`, put the settled numbers in named
constants at the top, and regenerate the whole set when one changes.

[`tools/gen_splitters.py`](../../../tools/gen_splitters.py) is the worked
example: one geometry definition, three styles, two libraries, with every tuned
parameter (`FAN_LAND`, `TREE_SHIFT`, `SQ_RISER`) named and commented.

Validate the output against the application's own parser rather than by eye
alone — `ComponentDef.from_svg` for port maps and `ComponentLibrary.scan` for
discovery. A cheap check that catches the common mistake: every lead endpoint
must coincide with a port coordinate, or `ComponentItem._shorten_leads_in_svg`
will silently skip that lead.

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

![Annotated resistor: all layers visible and color-coded — artwork (black), leads (teal), ports (red), label anchor (orange) — with viewBox boundary shown](fig-resistor-annotated.svg)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <defs>
    <style>
      .stroke { fill: none; stroke: #000; stroke-width: 3px;
                stroke-linecap: butt; stroke-linejoin: round; }
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
          stroke="#000" stroke-width="3" stroke-linecap="butt"/>
    <line x1="105" y1="10" x2="120" y2="10"
          stroke="#000" stroke-width="3" stroke-linecap="butt"/>
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

## Compound Components (Create from Selection)

You can create a new reusable component from a selection of existing components, connections, and annotations using **File > Create Component from Selection** or the right-click context menu.

**How it works:**

- The selected circuit is rendered as flat SVG artwork — all visual state (including stretched components) is captured at their current dimensions
- Unterminated ports (ports with no connection or connected outside the selection) become the new component's port markers
- The result is a standard SVG component file with `<g id="artwork">` and `<g id="ports">` layers

**Important notes:**

- Compound components are **not stretchable** — the exported SVG contains flat geometry with no break lines. Any stretchable sub-components are baked at their current stretched length.
- Connections are rendered as artwork — they become part of the visual body, not interactive wires.
- To modify a compound component, edit the original circuit and re-export.
