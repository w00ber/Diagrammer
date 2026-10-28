# Wiring interaction model — redesign

Status: **steps A–E landed** on branch `refactor/wiring-interaction-model`
(443 tests green). Baseline: `main` at `6852007` (434 tests green).

Before → after, same metrics as the "Structural cost" section below:
`mousePressEvent` 480 → 345 lines; `mouseReleaseEvent` 193 → 125;
drag implementations 4 (+ nudge + alt-clone) → 1 (`DragSession`);
drag-state fields on the view 13 → 2; `_skip_snap` sites 94 → 0;
`itemChange` position-snap overrides 4 → 0; per-operation junction
policies 9 → 1 (`movable_closure`); `isinstance(..., JunctionItem)`
55 → 38; `view.py` 2819 → 2398 lines. Net over `src/`: 18 files changed, 859 insertions(+), 1566 deletions(-).

## Why

Over several months every fix to wire/selection/drag behaviour has
introduced a subtler bug. In October 2026 a single week of user reports
covered: vertices left behind after a group drag, wires that would not
straighten onto the grid, a first undo that produced wires shooting off
to one side, paste leaving annotations and junctions behind on undo,
rubber-band + drag tearing rails off their junctions, and select-all +
drag tearing every tap in a ladder.

Each was reproduced headlessly against the user's own `.dgm` files and
fixed in `6852007`. Tracing them back, every one was a consequence of
one of **three design decisions**, and every fix — including the ones in
that commit — is a patch layered on one of them. This document names
the three, states the target model, and lays out a staged plan in which
each step deletes more code than it adds.

### Observed failures and their roots

| Symptom | Root decision |
| --- | --- |
| Vertices "left behind"; wires shear into staircases on drag | 2 — waypoints bound to nearest port |
| First undo after a drag displaces wires by the drag delta | 2 — re-bound in the wrong frame |
| Wiring grows more convoluted the more it is edited; runs won't straighten | 3 — router output stored as waypoints |
| Rubber-band + drag tears rails off their junctions | 3 — free-end grab handle is a waypoint |
| Select-all + drag tears every tap | 1 — junctions not selectable, so inferred per operation |
| Undo of paste leaves junctions and annotations behind | 1 — junctions added outside the command system |

### Structural cost today

- `DiagramView.mousePressEvent` is 480 lines; `mouseReleaseEvent` 193.
- Four drag implementations (movable-item, grouped-wire, alt-clone,
  unified) plus nudge plus `ConnectionItem`'s own waypoint/segment drag,
  each snapshotting state and committing undo differently; 13
  drag-state fields on the view that must agree across them.
- `isinstance(..., JunctionItem)` at 55 sites across five modules — nine
  independent policies for "which junctions move with this operation".
- `_skip_snap` set and reset at 94 sites: snapping lives in
  `itemChange`, so every programmatic move must disarm it.
- Undo macros hand-assembled per call site; one bug was purely about
  push order.

## The three decisions

### 1. Junctions are first-class topology but second-class UI objects

Every wire ends on a `PortItem`, and a free end or a tee is a `PortItem`
on a `JunctionItem`. But `JunctionItem` has no `ItemIsSelectable` flag
and is invisible unless three wires meet or the user set an end marker.
So "which junctions move" can never come from the selection; each
operation infers it:

| Operation | Policy |
| --- | --- |
| Drag | `_terminal_junctions_to_auto_include` |
| Rubber band | deselect junctions outside the rect (they were never selected) |
| Copy | `_gather_connected_junctions` |
| Alt-drag duplicate | endpoint inclusion inside `_duplicate_selection` |
| Delete | `delete_items_with_dependents` |
| Drag, external wires | `_trim_external_conn_endpoint_wps` |
| Drag, external wires | `_hold_external_conn_waypoints` |
| Wire's own drag | `ConnectionItem._move_endpoint_junctions` |
| Everywhere | the free-end grab-handle waypoint (see 3) |

They disagree, and each bug fix adds one.

### 2. Wire shape is stored port-relative, bound to the *nearer* endpoint

`Waypoint(anchor, dx, dy)` with `anchor` chosen by `_make_anchor` as
whichever endpoint port is nearer at write time. The stated purpose is
that waypoints follow a rotated or flipped component. In practice:

- every interaction thinks in scene coordinates and converts on every
  write, so a waypoint silently switches anchors mid-drag;
- a long rail's bends are split between its two ends, and half of them
  move when either end moves — the shear;
- an undo entry holding scene points re-binds against whatever the
  ports are doing when it runs, which inside a move macro is the wrong
  frame — the displaced-undo bug;
- compensating machinery accumulated: `_refresh_from_anchors`, trim at
  drag start, hold in scene space during drag, anchor-snapshot undo
  commands, `_SetAnchorsCommand` in `transform_ops`.

The rotation case it exists for is *already* handled explicitly for
groups by `_capture_internal_wire_shapes` / `_apply_internal_wire_shapes`.

### 3. Derived state is written back as source state

- `_store_expanded_as_waypoints` turns every auto-routed bend into a
  permanent user waypoint after each segment drag. `simplify_waypoints`
  now prunes the redundant ones, but the pattern remains.
- A free end's grab handle is a waypoint at offset (0, 0) from its
  junction — a UI affordance in the data model, indistinguishable from
  a bend to every other subsystem. Simplify, rubber-band, trim and
  dedup all special-case it.
- Lead-approach stubs live inside `_expanded`, so `routed_points()`
  exists to strip them out again.

## Target model

Five invariants. Everything below is a consequence of them.

**I1. A junction is an item.** Selectable, hit-testable with a small
radius, drawn as a handle when selected or when it is a free end. It
appears in `selectedItems()` like anything else; a rubber band selects
the ones inside it. There is no grab-handle waypoint.

**I2. Waypoints are scene coordinates.** `ConnectionItem._waypoints` is
the source of truth. Nothing re-binds. Moving one endpoint rubber-bands
the last leg to the first waypoint (the behaviour of every EDA tool).
Moving a set of items translates the waypoints of wires internal to
that set. Rotating or flipping a *group* transforms internal waypoints
explicitly, as it does today.

**I3. Only intent is stored.** The router derives the drawn route from
`[source, *waypoints, target]`. A segment drag records the dragged
segment's new coordinate, not the whole route. Approach stubs are
produced at paint time and never enter `_expanded`.

**I4. One connectivity rule.** `movable_closure(selection)` returns the
items, junctions, internal wires and boundary wires for an operation,
by the rule *the connected sub-circuit you selected moves, up to the
first component you did not select* (reach outward from selected items
along junction chains; prune any junction touching an unselected
component until stable). Drag, nudge, copy, cut, duplicate, delete and
transform all call it. No other policy exists.

**I5. One drag pipeline, snapshot/diff undo.** Press → closure →
snapshot positions and wire shapes → move loop applies the delta and
re-routes boundary wires → release → diff against the snapshot → a
single `MoveSelectionCommand`. Unified waypoint drag, alt-clone, nudge
and group moves are parameters to this pipeline, not branches. Undo
entries are built from the diff, never hand-ordered.

### Rotation of a single component (the question I2 decides)

The one visible behaviour change I2 brings. Setup: `R` with a wire from
its right port that bends at `B` and runs down to junction `J`.

```
        ┌───┐
        │ R ├────┐ B
        └───┘    │
                 │
                 ● J
```

Rotate `R` alone by 90°. Today (`B` bound to R's port, so it rotates
about R too):

```
        ┌─┐
        │R│
        └┬┘
    B ┌──┘            B swung round with R;
      │               the router then draws
      └──────────● J  something from B to J
```

Proposed (`B` stays; the last leg rubber-bands from the rotated port):

```
        ┌─┐
        │R│
        └┬┘      B
         └───────┐
                 │
                 ● J
```

If the whole shape should rotate, select the wire with `R` — the group
transform already does exactly that and is unaffected.

## Plan

Each step is independently shippable and leaves the suite green. The
two user `.dgm` files that reproduced the October bugs are committed as
fixtures and replayed by tests at every step.

| Step | Change | Deletes | Est. |
| --- | --- | --- | --- |
| A | `movable_closure` as the single connectivity rule; route copy/cut/duplicate/delete through it | the eight other junction policies | 1 day (drag half done in `6852007`) |
| B | Junctions selectable and hit-testable; free-end handle rendered, not stored | grab-handle waypoint and every special case for it; rubber-band junction rule | 2 days |
| C | Scene-coordinate waypoints | `Waypoint`, `_make_anchor`, `_refresh_from_anchors`, hold, trim, anchor snapshots, `SetWaypointAnchorsCommand`, `_SetAnchorsCommand` | 3 days |
| D | One drag pipeline with snapshot/diff undo | three of four drag branches, nudge's own undo assembly, most drag-state fields, most `_skip_snap` sites | 3 days |
| E | Segment drag stores only the dragged coordinate; approach stubs at paint time | `_store_expanded_as_waypoints`, `routed_points()` vs `all_points()` split | 1 day |

Order: A → B → C → D → E. A and B do not depend on the rotation
decision; C does.

## Test strategy

- `tests/fixtures/*.dgm` — the real files. Tests load them, replay the
  exact gesture (rubber band, drag, undo, paste) through the view's
  event handlers, and assert rigidity (every moved point shifts by the
  same delta) and round-trip (undo restores the pre-gesture geometry
  exactly).
- Property: after any drag, the set of waypoints is unchanged for every
  wire not in the closure.
- Property: after any undo, serialized geometry equals the pre-gesture
  serialization.

## Decisions log

- 2026-10-27: Rotation question put to the user with the pictures
  above; answer pending. A and B proceed regardless.
- 2026-10-27: **Step A landed** (`33309be`). `movable_closure` is the
  rule; drag, unified drag, nudge, copy and duplicate call it. Grouping
  and the transforms go through `_gather_connected_junctions`, now a
  wrapper over the same rule with `include_touching`.
- 2026-10-27: **Step B landed.** `JunctionItem` is selectable, has a
  pick disc, paints a handle while it or an attached wire is selected,
  and is never created hidden. The free-end grab-handle waypoint is
  gone: `_store_expanded_as_waypoints` treats a junction endpoint like
  a port, `simplify_waypoints` has no endpoint exemptions, and the
  serializer heals legacy handle waypoints on load. A rubber band now
  selects the junctions it covers, so the boxed tee of a rail travels
  with the box and its rail segment rubber-bands — the test for the
  user's partial-selection gesture was updated to say so. Deleting a
  bend no longer tows a junction. Locked layers lock junctions too.
  Hit-testing prefers a junction over the wires meeting at it.
- 2026-10-27: User confirmed the rotation picture: a lone component's
  rotation leaves the bend where it is and rubber-bands the last leg.
- 2026-10-27: **Step C landed.** Waypoints are scene coordinates.
  Deleted: `Waypoint`, `_make_anchor`, `_refresh_from_anchors`,
  `_set_waypoint_at`/`_pop_waypoint_at` rebinding, the anchor-snapshot
  API, `SetWaypointAnchorsCommand`, `_SetAnchorsCommand`,
  `_hold_external_conn_waypoints`, `_trim_external_conn_endpoint_wps`,
  and three of the view's drag-state dicts. `EditWaypointsCommand`
  (scene points) is order-independent by construction because nothing
  re-binds; the drag records every reshaped wire against one snapshot
  taken at press. Format 2.5 writes `[x, y]` waypoints — the v1 form
  every 2.x reader accepts — and still reads the 2.0 port-relative form
  through the port's frame. Clipboard and compound manifests likewise.
  Rotating one component now rubber-bands its wires (I2); group
  transforms map internal wires explicitly as before.
- 2026-10-27: **Step D1 landed.** `DragSession` (`canvas/drag.py`) is
  the one drag pipeline: snapshot at construction, idempotent
  `move_to(delta)` against the snapshot, `commit(label)` diffs the
  scene into a single macro (item moves, auto-join, healing, wire
  edits) or does nothing for a click. Plain, multi-select, grouped-
  wire, Alt-clone and unified-waypoint drags and the arrow-key nudge
  all drive it; the view keeps one `_drag` field and decides only the
  movers and the snapped delta (`_snapped_drag_delta`). Deleted: the
  second move branch, `_drag_changed_anything`, the hand-assembled
  release macro, scene `record_move_start/end`, ten drag-state fields
  and the vestigial `_alt_drag_clone`.
- 2026-10-27: **Step D2 landed.** Snapping is an input concern. The
  four `itemChange` position-snap overrides are gone, nothing is
  `ItemIsMovable` any more (Qt never moves an item; the session does),
  and every one of the 88 `_skip_snap` sites with them. The two places
  that snap are `DiagramView._snapped_drag_delta` (a drag lands the
  anchor's port on the grid) and `AddComponentCommand(snap_port_to_grid=
  True)` from the library drop. A programmatic `setPos` — move command,
  alignment, transform, paste — is exact. A locked layer is simply
  "not selectable".
- 2026-10-27: **Step E landed.** `_expanded` is the route from port
  to port and nothing else; the render-only approach stubs are added
  in `rebuild_path` (`_painted_points`), so `routed_points()` is now an
  alias of `all_points()` and the segment-drag clamp lost its
  duplicate-point workaround. A segment drag adopts the dragged
  polyline through `_adopt_route`, which keeps only the corners the
  router would not place on its own (I3) — a wire dragged back into
  the router's own shape ends up with no waypoints at all, and a wire
  no longer stays pinned to every old corner when an endpoint later
  moves.

- 2026-10-27: **Stacked-copy explosion fixed.** A file came in with
  identical capacitors stacked sixteen deep and C(16, 2) wires per
  geometry. Seed: undoing an Alt-drag duplicate reverted only the move,
  leaving the clones exactly on their originals; multiplier: the port
  auto-join wired every coincident copy to every other on the next
  drag. Now an Alt-drag is one undo entry (the "Duplicate" macro spans
  the drag), an Alt-click yields a copy offset by one grid step, and
  `auto_join_overlapping_ports` only joins ports the move *newly*
  brought together and never two items of the same drag. Paste no
  longer rebuilds every route per pushed command, and the drag loop
  re-routes only the wires it touches. `tools/dedupe_diagram.py`
  repairs an affected file.

- 2026-10-27 (post-0.12.0): Transforms and grouping now use the drag
  rule directly; `_gather_connected_junctions` is gone, and
  `include_touching` remains only for copy and duplicate, where nothing
  stays behind to pin a junction. `DiagramScene.merge_coincident_
  junctions` runs on load and collapses stacked junctions, re-pointing
  their wires and dropping the self-loops and duplicates that result.

## What is left

Candidates for a later pass, none of them load-bearing:
`ConnectionItem` is still {os.environ['M_CONN']} lines and could lose its
own segment/waypoint drag in favour of a `DragSession` variant; the
closed-polygon code paths duplicate the open-wire ones in several
places.
