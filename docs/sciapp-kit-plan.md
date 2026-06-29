# Plan: A shared framework + skill for scientist drawing/plotting apps

> Design plan for `sciapp-kit`, a reusable PySide6 + matplotlib framework extracted
> from Diagrammer and Graphulator/Paragraphulator. Committed here for continuity so a
> future Claude Code session (scoped to `sciapp-kit`, `diagrammer`, and `graphulator`)
> can pick up the build. Note: "Paragraphulator" is the `graphulator_para` entry point
> inside the `graphulator` repo, not a separate repo — so there are two source repos.

## Context

You maintain three PySide6 + matplotlib scientific desktop apps — **Diagrammer**,
**Graphulator**, and **Paragraphulator** — that share a set of interaction patterns
you like (keyboard-driven editing, a drawing canvas, matplotlib/LaTeX display, vector
export). You want to build *more* apps in this vein without reinventing those patterns,
and you're considering using **local LLMs** to build them later.

**Both questions answered by the research: yes, both are feasible.** Exploration of
`/home/user/Diagrammer` and `/home/user/graphulator` confirms a large, real body of
shared infrastructure. The catch — and the key design decision — is that the shared
concepts exist in **two different dialects** (e.g. Graphulator mutates a config module's
globals for settings; Diagrammer uses a typed settings object). So this should be a
**convention-and-interface library, not a "move the files" library**: normalize the
shared APIs, lift-and-shift only the genuinely generic pieces, and bake the conventions
into a scaffold + a Claude Code skill so future apps (including local-LLM-built ones)
come out consistent.

Hosting target: a new **`w00ber/sciapp-kit`** repo.

## What the two apps actually share (validated)

Both: Python 3.10+, PySide6 (Qt6), matplotlib, src-layout, setuptools + PyInstaller,
`~/.<appname>/` settings, versioned JSON document format, recent-files/autosave,
light/dark/system theming, centralized platform-aware rebindable keyboard shortcuts,
multi-format vector export (SVG/PNG/PDF + clipboard), interaction-mode state machines,
undo/redo, custom Qt widgets, heavily keyboard-driven UX.

| Concern | Graphulator | Diagrammer | Library strategy |
|---|---|---|---|
| Settings | `SettingsManager` mutates config module globals (`para_core/settings_manager.py`) | typed `AppSettings` + YAML defaults (`defaults.py`) | **Normalize → Diagrammer's typed object model** |
| Shortcuts | `ShortcutManager(QObject)` + dataclass defs (`para_ui/shortcut_manager.py`) | module-level `SHORTCUTS` dict (`shortcuts.py`) | **Normalize → keep Graphulator's manager, blended constructor** |
| Clipboard | pyobjc NSPasteboard (`clipboard_export.py`) | ctypes→pyobjc→subprocess→Qt cascade (`io/exporter.py`) | **Lift Diagrammer's (it's the robust superset)** |
| Theming | per-palette settings tabs | clean `apply_theme()` in `app.py` | **Lift Diagrammer's near-verbatim** |
| Canvas | matplotlib `FigureCanvasQTAgg` wrapper `MplCanvas` (`para_ui/canvas.py`) | `QGraphicsScene`/`QGraphicsView` (`canvas/scene.py`, `view.py`) | **Protocol + two adapters (see below)** |
| Math render | KaTeX-in-QWebEngine + SymPy printer | ziamath→usetex→mathtext | **Pluggable `MathRenderer`; KaTeX is optional extra** |
| Undo | JSON-snapshot stack | `QUndoStack`/`QUndoCommand` | **Standardize new apps on QUndoStack; offer `SnapshotCommand` helper** |
| **Help/Tutorial docs** | markdown + `{{shortcut:id}}` placeholder substitution, cached (`para_ui/doc_template.py`, `help_para.md`, `tutorial.md`) | modeless `HelpWindow`: markdown→HTML (lib + fallback), search, themed CSS, auto-appended shortcuts table (`panels/help_window.py`, `docs/help.md`) | **Lift `HelpWindow` + merge in placeholder substitution** |
| **Examples menu** | bundled `examples/graphs/*.graph`, `examples/pgraphs/*.pgraph` loaded from menu | `ExamplesDialog` preview-card grid, opens pick as *new untitled* doc (`panels/examples_dialog.py`, `examples/*.dgm`) | **Lift `ExamplesDialog`, parameterize by extension + loader/preview hooks** |

## Recommended approach: `sciapp-kit` (3 layers)

### Layer 1 — Shared library (`sciapp-kit`, import `sciappkit`)
src-layout. Each module tagged lift-and-shift (LS), normalized-API (NA), or new-glue (NG).

```
src/sciappkit/
  app/        application.py(NA/LS) theming.py(LS) main_window.py(NG: SciAppMainWindow base) fonts.py(LS)
  settings/   store.py(NA: typed SettingsStore) defaults.py(LS) recent_files.py(NA)
  shortcuts/  model.py(NA) manager.py(NA) editor.py(LS) help_doc.py(NG)
  docs/       help_window.py(NA: modeless markdown HelpWindow — search, themed CSS, lib+fallback render, link routing)
              template.py(LS: {{shortcut:id}} substitution + create_shortcut_reference_table; from Graphulator doc_template.py)
  examples/   dialog.py(NA: ExamplesDialog preview-card grid) loader.py(NG: list_example_files(ext), open-as-untitled convention)
  export/     base.py(NG: Exporter protocol) mpl_exporter.py(LS) scene_exporter.py(LS) clipboard.py(LS)
  canvas/     protocol.py(NG: CanvasController) mpl_canvas.py(LS) scene_canvas.py(NA) grid.py(LS) interaction.py(NA)
  undo/       stack.py(NG: QUndoStack + SnapshotCommand)
  math/       protocol.py(NG) mpl_backend.py(LS)[math] ziamath_backend.py(LS)[math] katex_backend.py(opt)[web]
  widgets/    spinbox.py(LS: FineControlSpinBox) text_edit.py(LS: LineNumberTextEdit)
  util/       color.py(LS) platform.py(NG)
```

**Deliberately small public API** (≈12 symbols — this is what makes local-LLM use tractable):
`create_app`, `SciAppMainWindow`, `apply_theme`, `SettingsStore`, `ShortcutRegistry`/`ShortcutManager`,
`MplCanvas`, `GraphicsCanvasBase`/`GraphicsViewBase`, `CanvasController`,
`Exporter`/`MplExporter`/`SceneExporter`, `copy_to_clipboard`, `MathRenderer`, `FineControlSpinBox`,
`HelpWindow`, `ExamplesDialog`.

**Help/Tutorial docs (shared convention).** `HelpWindow` (lifted from Diagrammer) is a modeless,
always-on-top markdown viewer: renders via the `markdown` lib with a built-in no-dep fallback,
themed "paper" CSS readable under dark mode, in-doc search, relative-image resolution, and `.md`
link routing. It auto-appends a **keyboard-shortcuts table generated live from the app's
`ShortcutRegistry`**, and also runs Graphulator's `{{shortcut:action.id}}` placeholder substitution
(`docs/template.py`) so inline shortcut mentions in prose stay current and "(custom)" overrides are
marked. `SciAppMainWindow` wires Help and Tutorial menu items to `HelpWindow.show_help()` /
`show_tutorial()`; apps just drop `docs/help.md` + `docs/tutorial.md` in their package.

**Examples menu (shared convention).** `ExamplesDialog` (lifted from Diagrammer) shows a preview-card
grid and opens the chosen file as a **new untitled document** so the read-only bundled file is never
overwritten by Save. It's parameterized by document **extension** (`.dgm`/`.graph`/`.pgraph`/…), a
**loader** callback, and a **preview-render** hook (mpl figure or scene→QImage), so any app reuses it.
Convention: bundled `examples/` dir resolved via the app's `_resources.py`, display name from filename,
wired to a "File → Examples…" menu item by `SciAppMainWindow`.

**Canvas abstraction (the core divergence).** Do *not* unify the two canvases under one
base class — Diagrammer's 2400-line view is mostly app-specific connection logic. Instead
define a thin **`CanvasController` Protocol** (`widget()`, `export_target()`, `exporter`,
`fit_to_content()`, `set_interaction_mode()`, `mode_changed` signal). Ship two adapters:
`MplCanvasController` (target = Figure, pairs with `MplExporter`) and `SceneCanvasController`
(target = QGraphicsScene, pairs with `SceneExporter`). The same `SciAppMainWindow` Export/Copy
menu drives either without knowing which. **This directly satisfies "both a drawing canvas
AND matplotlib display"**: an app can dock a `SceneCanvasController` editor *and* an
`MplCanvasController` plot panel, and export works uniformly on whichever has focus.
`GraphicsCanvasBase`/`GraphicsViewBase` ship only the generic ~300 lines (grid draw, zoom,
pan, snap, rubber-band, mode signal); all connection/port/waypoint logic stays in the app.

**Settings:** standardize on Diagrammer's typed `SettingsStore` (testable, discoverable,
IDE/LLM-autocompletable) over Graphulator's module-global mutation.

**Shortcuts:** keep Graphulator's `ShortcutManager(QObject)` (signals, conflict detect,
persistence) but use a per-app `ShortcutRegistry` (no global singleton) and a blended
constructor: `Shortcut(action_id, *, default=..., mac=..., win=..., linux=..., display_name=, category=, description=)`.

### Layer 2 — Project scaffold (`create-sciapp`)
A **copier** template (preferred over cookiecutter — supports update-in-place so scaffolded
apps can pull later template improvements). Emits a runnable skeleton wired to the library:
`pyproject.toml`, `src/<app>/{__init__,__main__,app,main_window,settings,shortcuts,canvas}.py`,
`defaults.yaml`, `docs/{help,tutorial}.md`, `examples/`, `README.md`, and a `CLAUDE.md`. A
`canvas_style` question (`scene` | `mpl` | `both`) selects which controller(s) to instantiate.
Conventions baked in: src-layout, `~/.<app>/` settings, `[project.scripts]` entry point,
PyInstaller-friendly `_resources.py` pattern, versioned JSON document stub, **Help/Tutorial menu
items wired to the `docs/*.md` stubs, and a File→Examples menu item wired to the `examples/` dir.**

### Layer 3 — Claude Code skill (`sciapp`)
> Build this in M2, together with the framework — not before. The skill documents the
> library's public API, so writing it earlier means writing it twice.
```
.claude/skills/sciapp/
  SKILL.md                    # when-to-use + build recipe + the ~12-symbol API cheat-sheet
  reference/api.md            # public API signatures + 1-line semantics
  reference/conventions.md    # src-layout, settings dir, shortcut IDs, theming, PyInstaller, Qt pinning caveat,
                              #   markdown help/tutorial docs (+ {{shortcut:id}} placeholders), examples/ + File→Examples convention
  reference/canvas.md         # picking scene vs mpl vs both; the CanvasController contract
  recipes/new_app.md          # run create-sciapp → wire a canvas → add a shortcut → add export
  examples/                   # 2-3 minimal runnable skeletons (one scene, one mpl, one both)
```
**Why this enables local LLMs:** the skill exposes a *small, strongly-conventioned* surface
with copy-pasteable recipes and runnable examples. A smaller local model never has to
navigate the 16k-line `graphulator_para.py`; it follows `recipes/new_app.md`, fills template
slots, and references `api.md`. Fixed settings path / shortcut-ID scheme / canvas contract
mean few degrees of freedom to get wrong. (2026 local options that can drive this agentically:
Qwen 3.6 27B or Devstral Small 24B on a 16GB+ machine; Codestral for autocomplete.)

## Risks / tradeoffs

- **Qt pinning (validated conflict):** Graphulator `PySide6>=6.4,<6.9`; Diagrammer
  `PySide6-Essentials>=6.8,<6.9` (documented Qt 6.11 QSvgRenderer/PDF regression). Library
  must pin the intersection `>=6.8,<6.9` and document the caveat. Diagrammer uses
  `PySide6-Essentials` (no WebEngine); Graphulator's KaTeX needs full PySide6 + QtWebEngine —
  so **KaTeX math must be an optional `[web]` extra**, never a base dependency.
- **Fourth codebase to maintain:** mitigated by keeping the library small (mostly LS + thin
  protocols).
- **Over-abstraction:** the real trap is forcing both canvases under one base class — the
  Protocol approach avoids it.
- **Don't break the existing apps:** they adopt nothing at first. Safest first adoption seams
  are `clipboard.py` (Graphulator → Diagrammer's robust cascade) and `app/theming.py`.
  Settings/shortcuts adoption stays optional and gradual. Semver; apps pin a minor range.

## Incremental rollout

1. **M0 — Create `w00ber/sciapp-kit`** (empty repo). Lift-and-shift the clean wins:
   `theming`, `clipboard`, `grid`, `defaults`, `mpl_canvas`, `FineControlSpinBox`. Pin Qt `>=6.8,<6.9`.
   (Claude authors `pyproject.toml`, `README`, `LICENSE`, and the `src/sciappkit/` skeleton.)
2. **M1 — Normalized APIs:** `SettingsStore`, `ShortcutRegistry`/`ShortcutManager`,
   `CanvasController` + two adapters, `SciAppMainWindow`. Unit-tested in isolation.
3. **M2 — Scaffold + skill:** copier template (`scene`/`mpl`/`both`) + `.claude/skills/sciapp/`
   with runnable examples. Validate by scaffolding a throwaway app end-to-end.
4. **M3 — Opportunistic dogfooding:** repoint Diagrammer's theming and Graphulator's clipboard
   to library imports (smallest, safest seams).
5. **M4 — Publish** sciapp-kit (PyPI or git URL); repoint apps from path/submodule to the package.

## Critical files (sources for extraction)
- `Diagrammer/src/diagrammer/app.py` — theming + `create_app` (source for `app/`)
- `Diagrammer/src/diagrammer/io/exporter.py` — scene exporter + robust clipboard cascade (`export/`)
- `Diagrammer/src/diagrammer/canvas/view.py` — defines the generic-vs-app-specific split for `scene_canvas.py`
- `Diagrammer/src/diagrammer/defaults.py` + `panels/settings_dialog.py` — typed settings model
- `graphulator/src/graphulator/para_ui/shortcut_manager.py` (+ `shortcut_definitions.py`, `shortcut_editor.py`) — shortcut system to normalize
- `graphulator/src/graphulator/para_ui/canvas.py` — `MplCanvas` (mpl side of the abstraction)
- `graphulator/src/graphulator/para_ui/widgets.py` — `FineControlSpinBox`, `LineNumberTextEdit`
- `graphulator/src/graphulator/clipboard_export.py` — mpl export side
- `Diagrammer/src/diagrammer/panels/help_window.py` — modeless markdown HelpWindow (source for `docs/help_window.py`)
- `graphulator/src/graphulator/para_ui/doc_template.py` — `{{shortcut:id}}` substitution (source for `docs/template.py`)
- `Diagrammer/src/diagrammer/panels/examples_dialog.py` — preview-grid + open-as-untitled (source for `examples/dialog.py`)

## Verification (when built)
- `pip install -e ./sciapp-kit` then `python -c "import sciappkit"` imports clean on Qt 6.8.
- Scaffold a throwaway app with each `canvas_style` (`scene`/`mpl`/`both`); each launches,
  draws, and Export→PDF/SVG/PNG + Copy-to-clipboard works via the shared menu.
- Unit tests for `SettingsStore` round-trip, `ShortcutRegistry` conflict detection +
  persistence, and the clipboard cascade fallbacks.
- Help/Tutorial: scaffolded app opens both docs from the menu; `{{shortcut:...}}` placeholders and
  the appended shortcuts table reflect a rebound key after editing it in Settings.
- Examples: File→Examples shows preview cards; picking one opens it as a new *untitled* doc and
  Save does not modify the bundled file.
- Dogfood seam: swap Diagrammer theming / Graphulator clipboard to library imports; both apps
  still run unchanged.
