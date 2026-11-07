# Diagrammer — notes for Claude

PySide6 desktop app for building technical diagrams (circuit schematics,
cryogenic wiring, flowcharts) from reusable SVG components on a snapping canvas.
See [README.md](README.md) for what it does and how to install it.

## Read this first, per task

| Working on | Read before touching code |
| ---------- | ------------------------- |
| SVG components (new parts, ports, leads, stretch) | **[src/diagrammer/docs/svg-component-spec.md](src/diagrammer/docs/svg-component-spec.md)** |
| Component validation / repair (checks, fixes, profiles) | **[src/diagrammer/svglint/](src/diagrammer/svglint/)** — one engine shared by the app, the skill script and the git hook; see its `__init__.py` |
| The user's component overlay (forks, tombstones, drift) | **[src/diagrammer/models/overrides.py](src/diagrammer/models/overrides.py)** |
| LaTeX / math annotations | [docs/math-annotations.md](docs/math-annotations.md) |
| User-facing behaviour | [docs/tutorial.md](docs/tutorial.md), [src/diagrammer/docs/help.md](src/diagrammer/docs/help.md) |
| Wire / selection / drag / undo interaction | **[docs/wiring-interaction-redesign.md](docs/wiring-interaction-redesign.md)** — the target model and the staged refactor; do not add another per-operation junction policy |

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

As of 2026-11-06 the suite is **fully green — 786 passed, 0 failed**. Any
failure you see is yours. Earlier notes here claimed 6 then 11 permanent
failures; both were measurement artifacts, from the traps below.

**Never measure a baseline in a `git worktree`.** The conda env has an editable
install whose `.pth` hardcodes this checkout's `src`:

```
~/miniforge3/envs/CMT/lib/python3.*/site-packages/__editable__.diagrammer-0.10.0.pth
  -> /Users/joe/Documents/PROGRAMMING/python/MY_PACKAGES/Diagrammer/src
```

So `pytest` run inside a worktree collects *that worktree's* tests but imports
*this* directory's `diagrammer`. Comparing a branch against `main` that way
pairs one tree's tests with another tree's source and invents failures wholesale
— it is what produced a bogus "47 failures across 5 root causes" reading of a
tree that actually had 10. To test another revision, either check it out here,
or point `PYTHONPATH` at the worktree's `src` and confirm
`python -c "import diagrammer; print(diagrammer.__file__)"` resolves where you
expect.

**Tests must not read `~/.diagrammer/settings.json`.** `app_settings` is a
singleton whose `__init__` ends in `load()`, so importing the app picks up
whoever is running it. The autouse `isolate_app_settings` fixture in
`tests/conftest.py` redirects `_SETTINGS_FILE` to a tmp path and calls
`reset_all()`, which also stops a stray `save()` from overwriting the real
file. Don't bypass it: with "Show junction dots" off in a real install, six
junction-dot tests fail on that machine and pass everywhere else.

**Two test-isolation traps beyond the settings file**, both of which cost real
time to diagnose:

**`gc.freeze()` in `_ensure_qapp` is load-bearing — do not remove it.** Without
it the suite segfaults *inside* a garbage collection once it is large enough.
The traceback reads `Garbage-collecting` and then points at whichever Qt or
PyObjC call came next, so it looks like a bug in component loading, in paste,
or in whatever you last touched. It is none of those.

The tell: **fourteen no-op `assert i == i` tests reproduce it as reliably as
fourteen real ones.** If you hit this, add ballast tests before you go hunting
in your own diff — the suite was sitting on a cliff at ~716 tests on this
machine and anything pushed it over. Freezing Qt's global state into the
permanent generation, where the collector never traverses it, removes the cliff
rather than moving it: verified with 60 ballast tests on top.

Things that look like fixes and are not: importing `AppKit` early (at conftest
scope or test-module scope) makes it *worse*, hoisting the `importorskip` makes
it worse, and `gc.disable()` around that import does nothing. Also don't import
`main_window` from an autouse fixture, which reorders Qt against PyObjC for
every test.

`isolate_component_overlay` redirects `~/.diagrammer/components/`, which
`MainWindow` now scans *first*. Without it, seven test modules would load
whatever the developer happens to have forked, and a test asserting on a
built-in component would measure that person's edit. It patches
`diagrammer.models.overrides`, which imports no Qt, rather than a name bound
inside `main_window` — see the trap above.

Assertions about a *displayed* shortcut go through the `native()` helper in
`tests/test_shortcut_overlay.py`. The app renders `NativeText`, so `Shift+F` is
`⇧F` on macOS and `Shift+F` on Linux; hardcoding either spelling makes the test
platform-specific while the app is correct on both.

## Conventions that bite

- **`MainWindow._scene` / `_view` / `_current_file` mean the *active* diagram.**
  Several diagrams can be open, one per tab; these are properties resolving to
  whichever is in front (see `document_tabs.py`), and they keep pointing at the
  last diagram while a library table or the Validation tab is showing. Anything
  window-wide (grid, snapping, background, routing default) must loop over
  `_documents()` *and* be applied to new tabs in `_configure_document`, or the
  second tab silently disagrees with the first. Connect a scene's signals in
  `_new_document`, not after it.
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
- **Validate component SVGs against the app's own parser**, not by eye. The
  checks live in `src/diagrammer/svglint/` and have three front ends: `python -m
  diagrammer.svglint <paths>`, the `svg-component` skill script (now a shim over
  the same engine), and Library ▸ Validate Component Libraries in the app.
  **`svglint` must never import PySide6** — the pre-commit hook may run under a
  bare `python3`, and `tests/test_svglint_engine.py` enforces it.
- **A new check must be selective or it is worse than nothing.** The bar is that
  some app code path misbehaves *silently*; an error means the part is broken on
  the canvas. Deliberately excluded: grid conformance (`--check-grid` warns on
  97 of 208 shipped files and most are off-grid by design), and body-weight
  consistency (House Conventions makes it inconsistent *per family* on purpose,
  so any threshold is wrong). Linecap is a warning, not an error, because 74
  shipped files still declare `round` mid-migration. There are tests asserting
  the shipped tree stays at one failing file and under 15% non-linecap warnings.
- **Never save a component through `io/compound_export.py`.** It emits only
  `artwork` + `ports` and exists to flatten a canvas selection; using it to save
  an *edited* component destroys leads, stretch, labels and the `<style>` block.
  Component writes go through `io/component_writer.py`, which substitutes into
  the original text so every untouched byte survives and the diff stays readable.
- **Library file operations stay out of the undo stack**, which is the one
  documented exception to the rule above. The stack belongs to a document and
  `undo()` must be infallible; un-deleting a file is not. Reversibility comes
  from tombstones, the Trash, and timestamped backups instead — the reasoning is
  in `panels/library_actions.py`.
- **`ComponentLibrary.scan` order *is* precedence.** A duplicate key is skipped,
  so scanning the user's overlay before the built-in tree is the entire
  copy-on-write mechanism. There is no resolver to find.
