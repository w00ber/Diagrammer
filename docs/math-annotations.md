# Math in Annotations

Diagrammer annotations support LaTeX math via dollar-delimiter syntax.
Double-click an annotation to edit it, then wrap math in delimiters:

| Syntax | Mode | Example |
|--------|------|---------|
| `$...$` | Inline math | `Voltage is $V = IR$` |
| `$$...$$` | Display math (centered, larger) | `$$\begin{bmatrix}A & B \\ C & D\end{bmatrix}$$` |

When you finish editing, the math expressions are rasterized to vector SVG
and rendered into the diagram at any zoom level without quality loss. The
original LaTeX source is preserved so you can re-edit by double-clicking.

## Rendering backends

Diagrammer picks a renderer in this order:

1. **ziamath** (pure-Python, bundled by default) — used for "pure" display
   math expressions, including `bmatrix`, `pmatrix`, `align`, etc. No system
   install required. This is the default for `$$...$$` blocks and is what
   ships with the standalone macOS / Windows builds.
2. **matplotlib mathtext** (built into matplotlib) — used for inline `$...$`
   math and as a fallback. Supports a fixed subset of LaTeX (`\frac`,
   `\sqrt`, `\sum`, Greek letters, etc.) but no environments like
   `bmatrix`. No system install required.
3. **matplotlib usetex** (shells out to a real LaTeX install) — optional
   path for users who want to render the full LaTeX language with their
   own preamble, fonts, and packages. Requires a system LaTeX
   distribution and Ghostscript on `PATH`.

## Optional: system LaTeX (`usetex` mode)

You only need this if you want matplotlib's full LaTeX pipeline — most
users can ignore it because ziamath already covers display math and
matrices. Enable it via **Settings → Annotations → "Prefer system LaTeX
over ziamath"**.

`usetex` mode shells out to three external programs that must all be
visible on the process `PATH`:

- `latex` (from MacTeX, TeX Live, or MiKTeX)
- `dvips`
- `gs` (Ghostscript)

**On macOS**, GUI apps launched from a `.app` bundle (Finder, Dock,
Spotlight) **do not inherit your shell `PATH`**, so even though
`/Library/TeX/texbin` works in Terminal, the bundled Diagrammer cannot
see it. Tell Diagrammer where to look explicitly:

1. Install MacTeX (`brew install --cask mactex` or
   <https://tug.org/mactex/>) and Ghostscript (`brew install ghostscript`).
2. Open **Settings → Annotations → LaTeX bin path** and set it to the
   directory containing the `latex` binary. For MacTeX this is usually:
   ```
   /Library/TeX/texbin
   ```
3. If `gs` lives in a different directory than `latex` (e.g.
   `/opt/homebrew/bin` or `/usr/local/bin`), make sure that directory is
   also reachable. The simplest fix is to symlink `gs` into the LaTeX
   bin directory, or to launch the app from a Terminal with the right
   `PATH` exported.
4. Click OK; the cache is invalidated automatically and the next math
   render picks up the new path. No restart needed.

**On Windows**, set **Settings → Annotations → LaTeX bin path** to the
directory containing `latex.exe`. For TeX Live this is usually:

```
C:\texlive\2024\bin\windows
```

For MiKTeX it's typically:

```
C:\Users\<you>\AppData\Local\Programs\MiKTeX\miktex\bin\x64
```

Ghostscript must also be installed separately (<https://ghostscript.com/>)
and either added to the system `PATH` or placed alongside `latex.exe`.

**On Linux**, install `texlive-latex-extra` (or equivalent) and
`ghostscript` from your package manager — they're already on `PATH` for
GUI launches, so you typically do not need to set the LaTeX bin path.

## Matrix typography knobs

When `usetex` mode is active, matrix layout uses two settings exposed in
**Settings → Annotations**:

- **`\arraycolsep`** — inter-column spacing inside `bmatrix`/`pmatrix`/
  `array`. The LaTeX default of 5 pt looks cramped after matplotlib's
  tight-bbox cropping; Diagrammer defaults to 48 pt.
- **`\arraystretch`** — row-height multiplier. LaTeX defaults to 1.0;
  Diagrammer defaults to 1.15.

These only affect `usetex` output. ziamath has its own internal layout
and ignores them.

## Troubleshooting

- *"Display math (`$$...$$`) was detected, but no renderer is
  available"* — ziamath should be bundled with the standalone builds
  and the `pip install` path. If you see this on a `pip` install,
  re-run `pip install -e .` to pick up the ziamath dependency.
- *Math annotations show nothing in a compiled `.app`* — ensure you
  are running a build of `claude/fix-swift-math-rendering-ix5uW` or
  later: earlier builds were missing `matplotlib.backends.backend_svg`
  in the PyInstaller spec, which silently broke all math rendering.
- *`usetex` mode does nothing after setting the LaTeX bin path* — open
  the app from a Terminal (`/Applications/Diagrammer.app/Contents/MacOS/Diagrammer`)
  to surface the matplotlib stderr, which usually says exactly which
  binary it could not find (`latex`, `dvips`, or `gs`).
