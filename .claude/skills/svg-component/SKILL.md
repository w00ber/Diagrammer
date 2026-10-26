---
name: svg-component
description: Author, modify, or review SVG component symbols for the Diagrammer library — the parts that appear in the library panel and get dropped onto the canvas. Use this whenever the request involves adding a new component or family of components (splitters, switches, couplers, filters, amplifiers, connectors, flowchart shapes), changing an existing symbol's geometry or line weights, fixing ports/leads that misbehave on the canvas, or picking between candidate symbol styles. Trigger it even when the user only says "add a part", "draw me a <device>", "make an N-way <thing>", "the wires attach from the wrong side", or names a specific file under src/diagrammer/components/ — and before hand-writing any SVG, since the conventions here are not guessable from reading existing component files.
---

# Diagrammer SVG components

Components are plain SVG files under `src/diagrammer/components/`. There is no
registration step — `ComponentLibrary.scan` walks the tree and every
subdirectory becomes a category in the library panel. Packaging already globs
`components/**/*.svg`.

That ease is a trap: a component can look perfect as a picture and still be
broken as a part, because the things that make it work are invisible. A port
two points off the viewBox edge makes wires attach from the wrong side. A lead
whose endpoint misses its port never shortens, so corners stop meeting cleanly.
A lead drawn at the wrong weight makes every wire step in thickness at the join.
None of this shows up until someone wires the part on the canvas.

## Read the spec first

**`src/diagrammer/docs/svg-component-spec.md`** is the source of truth: the
layer contract (`artwork` / `leads` / `ports` / `labels` / `stretch`), how ports
and approach directions are parsed, how lead shortening works, stretch axes,
and the Illustrator export traps. Its *House Conventions* section covers the
per-library line weights and the port grids the shipped families share.

Read it before writing SVG. Do not infer conventions by reading existing
component files — the libraries are not self-consistent, and you will copy
whichever accident you happened to open.

## The one rule the spec cannot enforce

Line weight is a property of the **library**, not the part. The same component
ships once per library so a figure reads correctly at its final size:

| Library | Leads |
| ------- | ----- |
| `PAPER_2pt` | 2 px |
| `SLIDES_3pt` | 3 px |
| `CRYO_WIRING_6pt` | 6 px |

`ConnectCommand._get_lead_stroke_width` reads this out of the component's CSS
and gives the attached wire the same width, which is why a mismatch is visible.
**A new part belongs in every library it is relevant to**, drawn at that
library's weight — not in one library only.

Body weight is *not* standardised (bodies range 1.6–3.2 px, and `SP6T` uses
2.7 px while `SPDT` uses 2 px). Open the specific neighbour the new part will
sit beside, read its body weight, and match that.

## Workflow

### 1. Find the neighbours

Identify the family the part joins and open one of its members. That tells you
the viewBox size, port grid, body weight and naming pattern to match. For the
circuit libraries this is usually: 80 pt wide viewBox, input at `x=0`, outputs
at `x=80` on a 20 pt pitch, body inset 10 pt with `rx="4"`.

Ask what the part should sit beside if it is not obvious — "next to the
switches" and "next to the couplers" imply different footprints.

### 2. Generate, don't hand-author

A part that exists at several N across several libraries is a cross product —
the splitters are 11 symbols times 2 libraries. Hand-authoring those drifts, and
a late change to one number means re-editing every file.

Write a generator under `tools/`, with every tuned number as a named constant at
the top. `tools/gen_splitters.py` is the worked example. When the user asks for
a corner to move by two points, that is then a one-line change and a re-run.

### 3. Show the work before installing

Geometry questions — how tight a corner reads, whether a symbol survives at 1:8,
whether it is distinguishable from the switch beside it — cannot be settled from
source. Generate candidates into a scratch directory, render them, and look.

```bash
python .claude/skills/svg-component/scripts/contact_sheet.py sheet.png --scale 2.6 \
    --row "Candidate A" out/A_1x2.svg out/A_1x4.svg out/A_1x8.svg \
    --row "Candidate B" out/B_1x2.svg out/B_1x4.svg out/B_1x8.svg \
    --row "Existing"    src/diagrammer/components/PAPER_2pt/circuits/µwave/switches/SP6T.svg
```

Then read the PNG. Always include the existing neighbour in the sheet — the
question is never "is this good" but "does this belong next to that".

When the user is choosing a style, give them a **ladder along one axis** rather
than scattered alternatives. Varying one parameter across four or five rungs
lets someone point at a rung; four unrelated designs only produce more
discussion. If a request is ambiguous ("smaller corner radii"), build the ladder
so it spans both readings instead of guessing which was meant.

### 4. Validate against the app, not your eyes

```bash
python .claude/skills/svg-component/scripts/validate_components.py \
    src/diagrammer/components/PAPER_2pt/circuits/µwave/splitters
```

This parses each file through `ComponentDef.from_svg` and checks ports land on
viewBox edges, lead endpoints coincide with ports, lead weights match the host
library, and no Illustrator wrapper `<g>` is hiding the named layers. Errors
mean the part will misbehave on the canvas; warnings may be deliberate (a
terminal's centred port is fine).

Run it on the whole tree occasionally — it currently finds four pre-existing
problems in the shipped libraries.

### 5. Check nothing else broke

```bash
QT_QPA_PLATFORM=offscreen ~/miniforge3/envs/CMT/bin/python -m pytest tests -q
```

The default `python3` has neither pytest nor PySide6. The suite is fully green,
so a failure is yours — but measure it *in this checkout*, never in a
`git worktree`: the env's editable-install `.pth` hardcodes this directory's
`src`, so a worktree run imports the wrong source. See the "Running tests"
section of [CLAUDE.md](../../../CLAUDE.md) for both of the traps here.

## Scripts

| Script | Use |
| ------ | --- |
| `scripts/validate_components.py` | Parse and check components the way the app does. Exits non-zero on error, so it can gate a commit. |
| `scripts/contact_sheet.py` | Compose SVGs into one captioned sheet (`--row` groups them) for visual review. `.png` output needs `rsvg-convert`. |

Plus, in the repo itself, `scripts/normalize_editor_svgs.py` — repairs the
`id`/primitive/wrapper mangling an SVG editor leaves behind; see *Things that go
wrong*.

Both take `--help`.

## Things that go wrong

**Illustrator wraps everything in `<g id="Layer_1">`**, which hides the named
layers from the parser. The spec also documents the "Use Artboards" export trap
(the viewBox silently grows to the artwork bounding box, pushing ports off the
edge) and the decimal-precision setting that kinks smooth curves. If a
hand-exported component behaves strangely, check those first.

**Round-tripping a component through a non-Illustrator editor can silently
un-make it.** VectorCraft, for one, does three things that each on their own
leave the part parsing as a picture with no ports and no stretch: it sanitizes
the colon out of `id="port:left"` (demoting the real name to
`data-name="port:left"`, which the parser never reads), it converts `<circle>`
and `<line>` into `<path>` (`_parse_ports` takes only `circle`/`rect`, stretch
only `line`), and it may re-wrap everything in `<g id="Layer_1">`. None of it
changes the rendered picture, so it survives review by eye.

`scripts/normalize_editor_svgs.py` reverses all three, losslessly and
idempotently:

```bash
python scripts/normalize_editor_svgs.py src/diagrammer/components --dry-run
```

Install `scripts/install_git_hooks.sh` once and a pre-commit hook normalizes
staged component SVGs and validates them, so a mangled export cannot reach
the history.

It also strips the `<metadata>` block Illustrator writes when **Preserve
Illustrator Editing Capabilities** is left checked on export. That block holds
`<i:aipgf>` — the whole document a second time as a zstd/base64 private stream,
45–90 KB against a median component of 1.5 KB. Nothing but Illustrator reads
it, so removing it cannot change the rendered symbol or what the parser sees;
what it costs is Illustrator's editing state on re-open, so keep the `.ai`
beside it if that matters. Uncheck the option and the problem does not arise.

Two cautions, both learned from the shipped tree. The script restores an `id`
*only* when it is the colon-sanitized form of the `data-name` — Illustrator uses
the same pair of attributes to de-duplicate a clashing id (`id="port:21"` with
`data-name="port:2"` is a **different** port from `port:2`, and rewriting it
would destroy one), so anything that is not a plain colon swap is left alone.
And its `--snap`/`--check-grid` grid alignment is opt-in: a part that centres a
port in a frame which is not a whole number of grid cells is off-grid by design,
and most of the library is like that, so `--check-grid` warns on 97 of 208 files
and `--snap` would rewrite 14 the validator already calls sound.

**Leads must end exactly on their port.** `_find_port_at` matches within 2 pt.
Put the weight transition on the body outline — leads spanning only the inset
(`x=0→10`, `x=80→70`) means the 2 px/3 px difference lands on the outline rather
than somewhere inside the body where it looks like a mistake.

**Caps are `butt`, and that changes how you draw a corner.** `round` and
`square` both overhang the endpoint by half the stroke width, which pokes out
past tee-junction joins and past the body outline. With butt caps, a right-angled
run drawn as separate `<line>` elements notches at every corner — draw it as one
`<path>` (`M … H … V … H …`) so the corners are joins rather than free ends.

**Lettering mirrors when the part is flipped.** Put it in a top-level
`<g id="upright">` *after* `leads` (inside artwork it would be painted over),
with `data-anchor="x,y"` on each child; the renderer cancels the flip and
rotation so the text stays readable and still moves with its port. Put a white knockout `<rect>` under each glyph — a rectangle, not a
white-stroked copy, which scallops any wire that grazes it — and size the
label's offset from the lead by the glyph's largest reach — the label stays upright
while its offset rotates, so a wide glyph like "LO" needs more room than a
narrow one like "I". `tools/gen_mixers.py` shows the whole pattern.

**Odd N needs a centre arm, powers of two aren't universal.** A binary cascade
only lands on N = 2, 4, 8; a fanout works at any N. If a family cannot cover
every N, say so rather than quietly shipping a gap.
