# Diagrammer — notes for Claude

PySide6 desktop app for building technical diagrams (circuit schematics,
cryogenic wiring, flowcharts) from reusable SVG components on a snapping canvas.
See [README.md](README.md) for what it does and how to install it.

## Read this first, per task

| Working on | Read before touching code |
| ---------- | ------------------------- |
| SVG components (new parts, ports, leads, stretch) | **[src/diagrammer/docs/svg-component-spec.md](src/diagrammer/docs/svg-component-spec.md)** |
| LaTeX / math annotations | [docs/math-annotations.md](docs/math-annotations.md) |
| User-facing behaviour | [docs/tutorial.md](docs/tutorial.md), [src/diagrammer/docs/help.md](src/diagrammer/docs/help.md) |

The component spec lives under `src/diagrammer/docs/` because it ships as in-app
help — not in the top-level `docs/`. It is the single source of truth for the
layer contract (`artwork` / `leads` / `ports` / `labels` / `stretch`), and its
"House Conventions" section covers the per-library line weights and port grids
that the shipped libraries follow. Do not infer these by reading example SVGs;
they are not self-consistent.

## Layout

```
src/diagrammer/
  app.py, main_window.py   application shell
  canvas/                  scene + view (routing, rubber-band, snapping)
  items/                   ComponentItem, ConnectionItem, PortItem, shapes
  commands/                QUndoCommand subclasses — every mutation goes through one
  models/                  ComponentDef (SVG parsing), ComponentLibrary (tree scan)
  io/                      .dgm serializer, SVG parse/export, compound export
  panels/                  library panel, properties, settings dialog
  components/<LIBRARY>/    the shipped SVG component tree
  docs/                    in-app help, including the component spec
tools/                     maintenance + generator scripts
scripts/                   one-off repair utilities
tests/                     pytest, needs a QApplication (see tests/conftest.py)
```

## Running tests

The default `python3` on this machine has neither pytest nor PySide6. Use the
conda env, and run headless:

```bash
QT_QPA_PLATFORM=offscreen ~/miniforge3/envs/CMT/bin/python -m pytest tests -q
```

As of 2026-10-22, **11** tests fail on a clean tree — 6 keybinding/overlay
expectations in `test_shortcut_overlay.py` and `test_wire_end_dots.py`, plus 5
junction-dot ones in `test_compound_export.py`, `test_crossovers.py` and
`test_tee_junction.py`. Verify against a clean checkout before assuming you
broke something, and prefer a throwaway worktree to a stash so uncommitted work
stays put:

```bash
git worktree add -f --detach /tmp/clean HEAD
(cd /tmp/clean && QT_QPA_PLATFORM=offscreen ~/miniforge3/envs/CMT/bin/python -m pytest tests -q)
git worktree remove --force /tmp/clean
```

## Conventions that bite

- **Every canvas mutation is an undo command.** Adding a feature that edits the
  scene directly instead of through `commands/` breaks undo silently.
- **Component SVGs are data, not code.** New parts are dropped into
  `src/diagrammer/components/` and picked up by `ComponentLibrary.scan` — no
  registration step. Packaging already globs `components/**/*.svg`.
- **A part belongs in every library it is relevant to**, redrawn at that
  library's line weight (`PAPER_2pt` 2 px, `SLIDES_3pt` 3 px,
  `CRYO_WIRING_6pt` 6 px). The weight is read out of the SVG's CSS at connect
  time and applied to the attached wire.
- **Families get a generator, not hand-authoring.** A part that exists at
  several N across several libraries is a cross product; see
  [tools/gen_splitters.py](tools/gen_splitters.py) for the pattern — settled
  numbers as named constants, regenerate the whole set when one changes.
- **Validate component SVGs against the app's own parser**, not by eye:
  `ComponentDef.from_svg` for the port map and `ComponentLibrary.scan` for
  discovery. The `svg-component` skill in `.claude/skills/` automates this.
