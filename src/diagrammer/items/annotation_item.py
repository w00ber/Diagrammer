"""AnnotationItem — editable rich-text label on the diagram canvas.

Double-click to enter edit mode (inline text editor).
Supports basic HTML formatting (bold, italic, font size, color).

Math mode: wrap LaTeX in $...$ delimiters. On finishing edit, math
expressions are rendered as vector SVG via matplotlib and displayed
using QSvgRenderer for resolution-independent quality at any zoom.
The source LaTeX is preserved for re-editing.
"""

from __future__ import annotations

import io
import logging
import re
import uuid

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPicture, QTextCursor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

logger = logging.getLogger(__name__)

SELECTION_PEN_COLOR = QColor(0, 120, 215)
SELECTION_PEN_WIDTH = 1.2
SELECTION_DASH_PATTERN = [4, 3]
DEFAULT_FONT_FAMILY = "Helvetica"
DEFAULT_FONT_SIZE = 12.0
DEFAULT_TEXT_COLOR = QColor(0, 0, 0)

# Regex to detect inline $...$ math (non-greedy, single line).
_MATH_RE = re.compile(r"\$(.+?)\$")

# Regex to detect display $$...$$ math (multi-line). Matched first so the
# greedy single-dollar pattern doesn't accidentally chew through it.
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)

# Top-level amsmath environments that provide their own math mode and must
# NOT be wrapped inside $...$.  Matches \begin{align}, \begin{align*},
# \begin{gather}, \begin{multline}, \begin{flalign}, \begin{alignat}{N},
# and their starred variants.
_TOPLEVEL_MATH_ENV_RE = re.compile(
    r"\\begin\{(align|align\*|gather|gather\*|multline|multline\*"
    r"|flalign|flalign\*|alignat|alignat\*)\}",
)

# Mapping from top-level amsmath environments to their "inner" equivalents
# that can be used inside math mode ($...$).  The inner environments
# produce the same visual layout but don't switch into/out of math mode
# themselves.
_TOPLEVEL_TO_INNER_ENV: dict[str, str] = {
    "align":      "aligned",
    "align*":     "aligned",
    "gather":     "gathered",
    "gather*":    "gathered",
    "multline":   "multlined",
    "multline*":  "multlined",
    "flalign":    "aligned",
    "flalign*":   "aligned",
    "alignat":    "alignedat",
    "alignat*":   "alignedat",
}


def _has_toplevel_math_env(text: str) -> bool:
    """Return True if *text* contains a top-level amsmath environment."""
    return bool(_TOPLEVEL_MATH_ENV_RE.search(text))


def _has_any_math(text: str) -> bool:
    return bool(
        _DISPLAY_MATH_RE.search(text)
        or _MATH_RE.search(text)
        or _TOPLEVEL_MATH_ENV_RE.search(text)
    )


def _has_display_math(text: str) -> bool:
    return bool(_DISPLAY_MATH_RE.search(text) or _TOPLEVEL_MATH_ENV_RE.search(text))


# Cache the result of the latex availability probe so we don't shell out
# on every render call.
_latex_available: bool | None = None
_ziamath_available: bool | None = None


def _ensure_latex_path_on_environ() -> None:
    """Ensure the system LaTeX binary is reachable via ``$PATH``.

    On macOS, GUI processes launched from a .app bundle (including
    PyInstaller bundles) don't inherit the shell PATH where
    ``/Library/TeX/texbin`` is added by MacTeX, so ``latex`` is not
    visible to ``shutil.which`` or matplotlib's usetex subprocess.

    Strategy:
    1. If the user set an explicit path in Settings → Annotations →
       LaTeX bin path, prepend that.
    2. Otherwise, probe well-known install locations for the current
       platform and prepend any that contain a ``latex`` binary.

    Calling this multiple times is safe; it deduplicates.
    """
    import os

    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep)

    def _prepend(d: str) -> None:
        nonlocal cur, parts
        if d and d not in parts:
            os.environ["PATH"] = d + os.pathsep + cur
            cur = os.environ["PATH"]
            parts = cur.split(os.pathsep)

    # 1. User-configured explicit path takes priority.
    try:
        from diagrammer.panels.settings_dialog import app_settings
        bin_dir = (getattr(app_settings, "latex_bin_path", "") or "").strip()
    except Exception:
        logger.debug("Could not load app_settings for latex_bin_path", exc_info=True)
        bin_dir = ""
    if bin_dir:
        _prepend(bin_dir)
        return

    # 2. Auto-detect well-known locations.
    import shutil
    import sys

    if shutil.which("latex"):
        return  # Already on PATH — nothing to do.

    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates = [
            "/Library/TeX/texbin",          # MacTeX default
            "/usr/local/texlive/2024/bin/universal-darwin",
            "/usr/local/texlive/2023/bin/universal-darwin",
            "/opt/homebrew/bin",            # Homebrew (Apple Silicon)
            "/usr/local/bin",               # Homebrew (Intel)
        ]
    elif sys.platform == "win32":
        # MiKTeX / TeX Live on Windows
        import glob
        candidates = glob.glob(r"C:\texlive\*\bin\windows")
        candidates += glob.glob(
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\MiKTeX\miktex\bin\x64"))
    else:
        candidates = [
            "/usr/local/texlive/2024/bin/x86_64-linux",
            "/usr/local/texlive/2023/bin/x86_64-linux",
            "/usr/bin",
        ]

    for cand in candidates:
        latex_path = os.path.join(cand, "latex")
        if sys.platform == "win32":
            latex_path += ".exe"
        if os.path.isfile(latex_path):
            _prepend(cand)
            return


def invalidate_latex_availability_cache() -> None:
    """Force the next ``_system_latex_available()`` call to re-probe.

    Called from the settings dialog after the user changes the
    ``latex_bin_path`` so the new path takes effect without restarting.
    """
    global _latex_available
    _latex_available = None


def _system_latex_available() -> bool:
    """Return True if a usable `latex` binary is on PATH.

    matplotlib's ``text.usetex`` mode shells out to ``latex`` + ``dvips`` +
    ``gs``; the cheapest reliable signal is the presence of ``latex``
    itself. We probe once and cache. The user-configured
    ``latex_bin_path`` is layered onto ``$PATH`` first so a custom
    install location is honored.
    """
    global _latex_available
    if _latex_available is None:
        _ensure_latex_path_on_environ()
        import shutil
        _latex_available = shutil.which("latex") is not None
    return _latex_available


def _ziamath_present() -> bool:
    """Return True if the optional ``ziamath`` package is importable.

    ziamath is a pure-Python LaTeX-math → SVG renderer that supports
    matrix environments without needing a system LaTeX install. It is
    our preferred backend for display math when available.
    """
    global _ziamath_available
    if _ziamath_available is None:
        try:
            import ziamath  # noqa: F401
            _ziamath_available = True
        except ImportError:
            _ziamath_available = False
    return _ziamath_available


# Track whether we've already shown the "no display math renderer" popup
# in this session, so we don't nag the user repeatedly.
_display_math_warning_shown: bool = False


def _maybe_warn_no_display_math_backend(parent_widget=None) -> None:
    """Show a one-time warning if display math is requested but no backend
    is available.

    Called by AnnotationItem when ``$$...$$`` is detected in the source
    text and neither ``ziamath`` nor a system LaTeX install can render
    it. The popup tells the user how to enable proper display-math
    rendering. Suppressed after the first call per session.
    """
    global _display_math_warning_shown
    if _display_math_warning_shown:
        return
    if _ziamath_present() or _system_latex_available():
        return
    _display_math_warning_shown = True
    try:
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(parent_widget)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Display math needs a renderer")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<b>Display math (<code>$$ ... $$</code>) was detected, but "
            "no renderer that supports environments like "
            "<code>\\begin{bmatrix}</code> is available.</b>"
        )
        msg.setInformativeText(
            "Two options to enable matrix / display math rendering:"
            "<ul>"
            "<li><b>Recommended (no install needed):</b> install the "
            "pure-Python <code>ziamath</code> package: "
            "<pre>pip install ziamath</pre></li>"
            "<li><b>Alternative:</b> install a system LaTeX distribution "
            "(MacTeX, TeX Live, MiKTeX) so matplotlib can use real "
            "LaTeX via its <code>usetex</code> mode.</li>"
            "</ul>"
            "Until then, the annotation will fall back to matplotlib's "
            "built-in <code>mathtext</code> renderer, which does not "
            "support matrix environments."
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
    except Exception:
        # If even showing the popup fails, log and move on.
        logger.warning("Display math detected but no renderer (ziamath/LaTeX) is "
                       "installed; falling back to mathtext.")

# Track which font fallback warnings have been shown (avoid repeating)
_font_warnings_shown: set[str] = set()


def _warn_font_fallback(requested: str, actual: str) -> None:
    """Print a one-time warning when a font isn't available."""
    if requested in _font_warnings_shown:
        return
    _font_warnings_shown.add(requested)
    logger.warning("Font '%s' not available — using '%s' as fallback.", requested, actual)

import sys as _sys

# Common font families grouped by category, with platform-appropriate choices
if _sys.platform == "darwin":
    FONT_FAMILIES = [
        "STIX Two Text", "CMU Serif", "CMU Sans Serif",
        "Helvetica", "Arial", "Verdana",
        "Times New Roman", "Georgia", "Palatino",
        "Courier New", "Menlo", "Monaco",
    ]
elif _sys.platform == "win32":
    FONT_FAMILIES = [
        "CMU Serif", "CMU Sans Serif",
        "Cambria Math", "Times New Roman", "Calibri",
        "Arial", "Verdana", "Segoe UI",
        "Georgia", "Palatino Linotype",
        "Courier New", "Consolas", "Lucida Console",
    ]
else:  # Linux
    FONT_FAMILIES = [
        "STIX Two Text", "DejaVu Serif", "Liberation Serif",
        "DejaVu Sans", "Liberation Sans", "Noto Sans",
        "DejaVu Sans Mono", "Liberation Mono", "Noto Mono",
    ]


# Map user font families to matplotlib mathtext fontsets.
# Serif fonts → 'cm' (Computer Modern) or 'stix'; sans → 'stixsans' or 'dejavusans'
_MATH_FONTSET_MAP = {
    "STIX Two Text": "stix",
    "CMU Serif": "cm",
    "CMU Sans Serif": "stixsans",
    "Times New Roman": "stix",
    "Georgia": "stix",
    "Palatino": "stix",
    "Helvetica": "stixsans",
    "Arial": "stixsans",
    "Verdana": "dejavusans",
    "Courier New": "stix",
    "Menlo": "dejavusans",
    "Monaco": "dejavusans",
}


def _is_pure_display_math(text: str) -> str | None:
    """If ``text`` is essentially one or more ``$$...$$`` blocks plus
    whitespace (no inline math, no surrounding prose), return the
    concatenated math body so we can hand it directly to ziamath.

    Returns None if the text mixes display math with prose or inline
    ``$...$`` math (in which case we have to fall back to a renderer
    that handles mixed text+math, like matplotlib's usetex/mathtext).

    Top-level amsmath environments (``align``, ``gather``, etc.) are
    converted to their inner equivalents (``aligned``, ``gathered``,
    etc.) so ziamath can render them.
    """
    stripped = text.strip()
    if not stripped:
        return None
    # Bare top-level environment (no $$ delimiters) — treat the whole
    # thing as a single math body after converting to the inner form.
    if _has_toplevel_math_env(stripped) and not stripped.startswith("$$"):
        body = " ".join(stripped.split())
        return _toplevel_env_to_inner(body)
    # Walk through and collect bodies; bail if anything non-blank lives
    # outside a $$ ... $$ block.
    bodies: list[str] = []
    pos = 0
    n = len(stripped)
    while pos < n:
        # Skip whitespace between blocks.
        while pos < n and stripped[pos].isspace():
            pos += 1
        if pos >= n:
            break
        if not stripped.startswith("$$", pos):
            return None
        end = stripped.find("$$", pos + 2)
        if end == -1:
            return None
        bodies.append(stripped[pos + 2:end])
        pos = end + 2
    if not bodies:
        return None
    body = r" \\ ".join(b.strip() for b in bodies)
    # Convert any top-level environments inside $$...$$ to inner form.
    if _has_toplevel_math_env(body):
        body = _toplevel_env_to_inner(body)
    return body


_SVG_NS_REGISTERED = False


def _ensure_svg_default_namespace_registered() -> None:
    """Tell ElementTree to serialize the SVG namespace as the default
    (``xmlns="..."``) instead of as a prefixed namespace
    (``xmlns:ns0="..."``).

    Qt's ``QSvgRenderer`` will silently refuse to render content whose
    elements live under a non-default-namespace prefix even when the
    URI is the correct SVG one. Without this, our normalize / inline
    rewrites round-trip the SVG into ``<ns0:svg ...>`` form and Qt
    produces a blank image.
    """
    global _SVG_NS_REGISTERED
    if _SVG_NS_REGISTERED:
        return
    import xml.etree.ElementTree as ET2
    ET2.register_namespace("", "http://www.w3.org/2000/svg")
    ET2.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    _SVG_NS_REGISTERED = True


def _normalize_svg_viewbox_origin(svg_bytes: bytes) -> bytes:
    """Rewrite an SVG so its viewBox starts at (0, 0).

    ziamath emits SVGs with viewBoxes like ``"0 -85 120 100"`` so that
    glyphs above and below the baseline have room. ``QSvgRenderer`` does
    not always honor non-zero viewBox origins when rendering into a
    target rect: depending on the Qt version, content can land outside
    the target rect, leaving our bounding/selection rect disjoint from
    the painted glyphs. We sidestep that by wrapping every root child
    in a ``<g transform="translate(-vx, -vy)">`` and resetting the
    viewBox to ``"0 0 w h"``. After this transform the content occupies
    exactly ``(0, 0, w, h)`` in painter coordinates regardless of how
    Qt interprets the viewBox.
    """
    _ensure_svg_default_namespace_registered()
    try:
        import xml.etree.ElementTree as ET2
        root = ET2.fromstring(svg_bytes)
    except ET2.ParseError:
        return svg_bytes

    vb = root.get("viewBox", "")
    if not vb:
        return svg_bytes
    parts = vb.replace(",", " ").split()
    if len(parts) != 4:
        return svg_bytes
    try:
        vx, vy, vw, vh = (float(p) for p in parts)
    except ValueError:
        return svg_bytes
    if vx == 0.0 and vy == 0.0:
        return svg_bytes  # already normalized

    SVG_NS = "http://www.w3.org/2000/svg"
    wrapper = ET2.Element(f"{{{SVG_NS}}}g")
    wrapper.set("transform", f"translate({-vx},{-vy})")
    # Move every existing child of the root into the wrapper.
    for child in list(root):
        root.remove(child)
        wrapper.append(child)
    root.append(wrapper)
    root.set("viewBox", f"0 0 {vw} {vh}")
    # Drop width/height attributes that may pin the SVG to a specific
    # render size and confuse the rect mapping.
    for attr in ("width", "height"):
        if attr in root.attrib:
            del root.attrib[attr]
    return ET2.tostring(root, encoding="utf-8")


def _inline_svg_use_refs(svg_bytes: bytes) -> bytes:
    """Replace every ``<use href="#id">`` with an inlined copy of the
    referenced ``<symbol>``/``<g>`` def, baking the use's ``x``/``y``/
    ``transform`` into a wrapping ``<g transform="translate(x,y) ...">``.

    ziamath emits glyphs as a small set of ``<symbol>``/``<g>`` defs and
    then references them many times via ``<use href="#g42" x="..."
    y="..."/>`` for deduplication. Qt's ``QSvgRenderer`` (SVG 1.2 Tiny)
    does not honor positional attributes on ``<use>``, so all reused
    glyphs collapse onto the origin and only directly-drawn paths
    (e.g. matrix brackets) appear at the right place. Inlining the
    references with explicit translate transforms sidesteps that bug.

    Returns the rewritten SVG bytes (or the input unchanged if no
    ``<use>`` references are present, or if parsing fails).
    """
    _ensure_svg_default_namespace_registered()
    try:
        import xml.etree.ElementTree as ET2
        root = ET2.fromstring(svg_bytes)
    except ET2.ParseError:
        return svg_bytes

    SVG_NS = "http://www.w3.org/2000/svg"
    XLINK_NS = "http://www.w3.org/1999/xlink"

    def _local(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    # Build id → element map for every potentially-referenced def. We
    # accept <symbol>, <g>, <path>, etc. — anything with an id attribute.
    id_map: dict[str, ET2.Element] = {}
    for el in root.iter():
        eid = el.get("id")
        if eid:
            id_map[eid] = el

    if not id_map:
        return svg_bytes

    def _resolve_href(use_el: ET2.Element) -> str | None:
        href = use_el.get("href") or use_el.get(f"{{{XLINK_NS}}}href")
        if href and href.startswith("#"):
            return href[1:]
        return None

    # Walk the tree and rewrite each <use> in place. We need parent
    # pointers (ElementTree doesn't track them) so build them manually.
    parent_map = {child: parent for parent in root.iter() for child in parent}

    import copy

    def _rewrite_use(use_el: ET2.Element) -> None:
        target_id = _resolve_href(use_el)
        if target_id is None:
            return
        target = id_map.get(target_id)
        if target is None:
            return
        # Avoid self-reference loops.
        if target is use_el:
            return

        x = use_el.get("x", "0")
        y = use_el.get("y", "0")
        existing_tx = use_el.get("transform", "")
        translate = f"translate({x},{y})"
        combined = (existing_tx + " " + translate).strip() if existing_tx else translate

        # Build a replacement <g> wrapping deep-copied children of the
        # target. If the target is a <symbol>, drop the symbol wrapper
        # and inline its children directly. Otherwise inline a copy of
        # the target itself (e.g. a <path id="g42">).
        wrapper = ET2.Element(f"{{{SVG_NS}}}g")
        wrapper.set("transform", combined)
        # Carry over fill/stroke/style attributes from <use> if any
        # (ziamath usually puts color on a parent so this is rarely
        # needed, but it's cheap insurance).
        for attr in ("fill", "stroke", "style", "opacity"):
            v = use_el.get(attr)
            if v is not None:
                wrapper.set(attr, v)

        if _local(target.tag) == "symbol":
            for child in target:
                wrapper.append(copy.deepcopy(child))
        else:
            clone = copy.deepcopy(target)
            # Strip the id from the clone so we don't end up with
            # duplicate ids in the rewritten document.
            if "id" in clone.attrib:
                del clone.attrib["id"]
            wrapper.append(clone)

        parent = parent_map.get(use_el)
        if parent is None:
            return
        # Replace use_el with wrapper in parent's children list.
        idx = list(parent).index(use_el)
        parent.remove(use_el)
        parent.insert(idx, wrapper)
        parent_map[wrapper] = parent

    # Collect uses first so we don't mutate the tree while iterating.
    uses: list[ET2.Element] = []
    for el in root.iter():
        if _local(el.tag) == "use":
            uses.append(el)

    if not uses:
        return svg_bytes

    for u in uses:
        _rewrite_use(u)

    # Optionally drop now-orphaned <symbol> defs to keep the SVG smaller.
    defs_parents: list[tuple[ET2.Element, ET2.Element]] = []
    for el in list(root.iter()):
        if _local(el.tag) == "symbol":
            p = parent_map.get(el)
            if p is not None:
                defs_parents.append((p, el))
    for p, sym in defs_parents:
        try:
            p.remove(sym)
        except ValueError:
            pass

    return ET2.tostring(root, encoding="utf-8")


def _toplevel_env_to_inner(text: str) -> str:
    r"""Replace top-level amsmath environments with their inner equivalents.

    ``\begin{align}`` → ``\begin{aligned}``, etc. The inner environments
    can be used inside math mode (``$...$``), whereas the top-level ones
    cannot.
    """
    def _env_repl(m: "re.Match") -> str:
        env = m.group(1)
        inner = _TOPLEVEL_TO_INNER_ENV.get(env, env)
        return r"\begin{" + inner + "}"
    text = _TOPLEVEL_MATH_ENV_RE.sub(_env_repl, text)
    # Also fix the matching \end{...} tags.
    for outer, inner in _TOPLEVEL_TO_INNER_ENV.items():
        text = text.replace(r"\end{" + outer + "}", r"\end{" + inner + "}")
    return text


def _convert_display_math_for_matplotlib(text: str, *, displaystyle: bool = True) -> str:
    r"""Rewrite ``$$ ... $$`` blocks into single-line inline math so
    matplotlib's ``text()`` artist can render them.

    matplotlib splits multi-line strings on ``\n`` and renders each
    line as a separate text run. If we leave a ``$$`` on its own line
    matplotlib feeds the literal two-character string to LaTeX, which
    rejects it as an empty display-math block ("Extra }, or forgotten
    \$"). We collapse each ``$$ ... $$`` body onto one line — LaTeX is
    whitespace-insensitive in math mode, so this is safe — and wrap
    it in ``$ ... $``.

    When ``displaystyle`` is True (the usetex path) we prepend
    ``\displaystyle`` so the math renders with display-style spacing
    instead of the smaller inline-math metrics. The mathtext fallback
    must NOT use ``\displaystyle`` because mathtext doesn't recognise
    that macro and will raise a ParseFatalException.

    Top-level amsmath environments (``align``, ``gather``, etc.) are
    converted to their inner equivalents (``aligned``, ``gathered``,
    etc.) so they can be wrapped inside ``$...$``.
    """
    # First, handle bare top-level environments (not inside $$...$$).
    # Convert them to $\displaystyle \begin{aligned}...\end{aligned}$
    if _has_toplevel_math_env(text) and not _DISPLAY_MATH_RE.search(text):
        body = " ".join(text.split())
        body = _toplevel_env_to_inner(body)
        if displaystyle:
            return r"$\displaystyle " + body + r"$"
        return r"$" + body + r"$"

    def _repl(m: "re.Match") -> str:
        body = " ".join(m.group(1).split())
        # Convert top-level environments to inner equivalents so they
        # can live inside $...$.
        body = _toplevel_env_to_inner(body)
        if displaystyle:
            return r"$\displaystyle " + body + r"$"
        return r"$" + body + r"$"
    return _DISPLAY_MATH_RE.sub(_repl, text)


def _render_with_ziamath(math_body: str, font_size: float,
                         color: QColor) -> bytes | None:
    """Render LaTeX math (no enclosing ``$$``) to SVG bytes via ziamath.

    Returns SVG bytes on success or None if ziamath isn't installed or
    parsing failed (e.g. unknown macro). The returned bytes have all
    ``<use>`` references inlined so Qt's ``QSvgRenderer`` can paint them.
    """
    if not _ziamath_present():
        logger.debug("ziamath not installed")
        return None
    try:
        import ziamath as zm
        mpl_color = f"#{color.red():02x}{color.green():02x}{color.blue():02x}"
        m = zm.Math.fromlatex(math_body, size=font_size, color=mpl_color)
        svg_str = m.svg()
        if not svg_str:
            logger.debug("ziamath returned empty SVG")
            return None
        raw = svg_str.encode("utf-8") if isinstance(svg_str, str) else svg_str
        logger.debug("ziamath produced %d bytes of SVG", len(raw))
        # Normalize viewBox origin to (0, 0) BEFORE inlining <use> refs,
        # so the translate wrapper sits at the SVG root and any nested
        # absolute coordinates in the inlined glyphs share the same
        # origin shift.
        normalized = _normalize_svg_viewbox_origin(raw)
        result = _inline_svg_use_refs(normalized)
        logger.debug("ziamath SVG after rewrite: %d bytes", len(result))
        # Dump intermediate forms so we can inspect what Qt is actually
        # being asked to render. These files survive between runs so the
        # user can open them in a real SVG viewer for comparison.
        try:
            import tempfile, os
            tmp = tempfile.gettempdir()
            with open(os.path.join(tmp, "diagrammer_math_raw.svg"), "wb") as f:
                f.write(raw)
            with open(os.path.join(tmp, "diagrammer_math_normalized.svg"), "wb") as f:
                f.write(normalized)
            with open(os.path.join(tmp, "diagrammer_math_final.svg"), "wb") as f:
                f.write(result)
            logger.debug("SVG snapshots written to %s/diagrammer_math_*.svg", tmp)
        except Exception:
            logger.debug("failed to dump SVG snapshots", exc_info=True)
        return result
    except Exception:
        logger.warning("ziamath render failed", exc_info=True)
        return None


def _render_latex_svg(text: str, font_size: float, color: QColor,
                      font_family: str = "serif") -> bytes | None:
    """Render a string (with $...$ or $$...$$ math) to SVG bytes via matplotlib.

    Two rendering modes are used depending on the input:

    * **mathtext** (matplotlib's built-in): inline ``$...$`` only, supports
      a fixed subset of LaTeX (``\\frac``, ``\\sqrt``, ``\\sum``, Greek
      letters, etc.). No environments like ``bmatrix``. Used when the
      annotation only has inline math.

    * **usetex** (shells out to a real LaTeX installation): supports the
      full LaTeX language including ``$$...$$`` display math and
      environments like ``\\begin{bmatrix} ... \\end{bmatrix}``. Used
      when the annotation contains any ``$$...$$`` block AND a system
      ``latex`` binary is on PATH.

    Returns SVG bytes or None if matplotlib is unavailable or rendering fails.
    """
    # Preferred path for display math: ziamath. It's pure-Python, supports
    # `\begin{bmatrix}` and friends, and doesn't need any system install.
    # We only take this path when the annotation is "essentially" pure
    # display math (one or more $$...$$ blocks with nothing but whitespace
    # around them), because ziamath only knows how to render math — not
    # mixed text+math like matplotlib does.
    # If the user has explicitly opted in to "Prefer system LaTeX over
    # ziamath", honor that and skip the ziamath path entirely.
    try:
        from diagrammer.panels.settings_dialog import app_settings
        prefer_latex = bool(getattr(app_settings, "prefer_system_latex_for_math", False))
    except Exception:
        logger.debug("Could not load app_settings for prefer_system_latex_for_math", exc_info=True)
        prefer_latex = False

    if _has_display_math(text) and not prefer_latex:
        pure_body = _is_pure_display_math(text)
        if pure_body is not None:
            svg = _render_with_ziamath(pure_body, font_size, color)
            if svg is not None:
                return svg

    try:
        import matplotlib
        matplotlib.use("agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib import failed", exc_info=True)
        return None

    # Make sure any user-configured LaTeX bin directory is on PATH
    # *before* matplotlib spawns its usetex subprocess. Without this,
    # matplotlib's `latex` invocation inherits the parent process's PATH
    # and may not find the binary on macOS GUI launches.
    _ensure_latex_path_on_environ()

    use_real_latex = _has_display_math(text) and _system_latex_available()
    logger.debug("rendering: display=%s, latex_available=%s, use_real_latex=%s",
                 _has_display_math(text), _system_latex_available(), use_real_latex)

    # matplotlib's text() artist (in both usetex and mathtext modes) can't
    # ingest $$...$$ blocks directly: it splits multi-line strings into
    # separate text runs and feeds the literal $$ to LaTeX, which errors.
    # Rewrite display blocks to single-line $...$ form. Keep the original
    # text around in case the usetex path fails and we need to retry
    # through mathtext (which can't handle \displaystyle).
    original_text = text
    if _has_display_math(text):
        text = _convert_display_math_for_matplotlib(
            text, displaystyle=use_real_latex)
        logger.debug("converted text for matplotlib: %r", text)

    # Snapshot rcParams we may touch so we can restore them after the call.
    saved = {
        "text.usetex": matplotlib.rcParams.get("text.usetex", False),
        "mathtext.fontset": matplotlib.rcParams.get("mathtext.fontset", "dejavusans"),
        "text.latex.preamble": matplotlib.rcParams.get("text.latex.preamble", ""),
    }
    try:
        if use_real_latex:
            matplotlib.rcParams["text.usetex"] = True
            # ``\begin{bmatrix}`` and friends live in amsmath, which
            # matplotlib's default usetex preamble does not load. Without
            # this line LaTeX errors with "Environment bmatrix undefined".
            # ``\arraycolsep`` and ``\arraystretch`` are user-tunable
            # via Settings → Annotations.
            try:
                from diagrammer.panels.settings_dialog import app_settings
                colsep = float(getattr(app_settings, "latex_arraycolsep_pt", 48.0))
                stretch = float(getattr(app_settings, "latex_arraystretch", 1.15))
            except Exception:
                logger.debug("Could not load app_settings for latex array params", exc_info=True)
                colsep, stretch = 6.0, 1.15
            matplotlib.rcParams["text.latex.preamble"] = (
                r"\usepackage{amsmath}"
                r"\usepackage{amssymb}"
                r"\usepackage{array}"
                r"\setlength{\arraycolsep}{" + f"{colsep}" + r"pt}"
                r"\renewcommand{\arraystretch}{" + f"{stretch}" + r"}"
            )
            # In usetex mode the font is controlled by LaTeX itself; we
            # don't need to set mathtext.fontset.
        else:
            matplotlib.rcParams["text.usetex"] = False
            matplotlib.rcParams["mathtext.fontset"] = (
                _MATH_FONTSET_MAP.get(font_family, "stix"))

        fig = plt.figure(figsize=(0.01, 0.01))
        mpl_color = f"#{color.red():02x}{color.green():02x}{color.blue():02x}"
        fig.text(
            0.5, 0.5, text,
            fontsize=font_size,
            color=mpl_color,
            family=font_family,
            ha="center", va="center",
        )
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", transparent=True,
                    bbox_inches="tight", pad_inches=0.02)
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        logger.warning("primary render failed (use_real_latex=%s)", use_real_latex,
                       exc_info=True)
        # If usetex was requested and failed (e.g. LaTeX install is broken
        # or the user's source has an unrecognized macro), retry once with
        # mathtext as a best-effort fallback so the annotation still
        # renders something instead of vanishing.
        if use_real_latex:
            try:
                matplotlib.rcParams["text.usetex"] = False
                matplotlib.rcParams["mathtext.fontset"] = (
                    _MATH_FONTSET_MAP.get(font_family, "stix"))
                # Re-convert without \displaystyle since mathtext can't
                # parse that macro.
                fallback_text = (
                    _convert_display_math_for_matplotlib(
                        original_text, displaystyle=False)
                    if _has_display_math(original_text)
                    else original_text
                )
                fig = plt.figure(figsize=(0.01, 0.01))
                mpl_color = f"#{color.red():02x}{color.green():02x}{color.blue():02x}"
                fig.text(
                    0.5, 0.5, fallback_text,
                    fontsize=font_size,
                    color=mpl_color,
                    family=font_family,
                    ha="center", va="center",
                )
                buf = io.BytesIO()
                fig.savefig(buf, format="svg", transparent=True,
                            bbox_inches="tight", pad_inches=0.02)
                plt.close(fig)
                return buf.getvalue()
            except Exception:
                logger.warning("mathtext fallback also failed", exc_info=True)
                return None
        return None
    finally:
        # Restore rcParams so other matplotlib users in the app aren't
        # affected by our temporary mode switch.
        for k, v in saved.items():
            matplotlib.rcParams[k] = v


class AnnotationItem(QGraphicsTextItem):
    """A rich-text annotation that can be placed and edited on the canvas.

    - Single-click to select (move, delete, etc.)
    - Double-click to enter inline text editing
    - Click away or press Escape to finish editing
    - Supports HTML: bold (Ctrl+B), italic (Ctrl+I), etc.
    - Math: wrap LaTeX in $...$ — rendered as vector SVG on edit finish
    """

    def __init__(
        self,
        text: str = "Text",
        instance_id: str | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._id = instance_id or uuid.uuid4().hex[:12]
        self._group_id: str | None = None
        self._group_ids: list[str] = []
        self._editing = False
        self._skip_snap = False
        self._source_text: str = text  # plain-text source (with $..$ math)
        self._math_renderer: QSvgRenderer | None = None  # vector math renderer
        self._math_image = None  # pre-rasterized QImage of the math SVG
        self._math_rect: QRectF = QRectF()  # natural size of the math SVG

        # Default font
        font = QFont(DEFAULT_FONT_FAMILY, int(DEFAULT_FONT_SIZE))
        self.setFont(font)
        self.setDefaultTextColor(DEFAULT_TEXT_COLOR)

        # Set initial text
        self.setPlainText(text)
        self._try_render_math()

        # Item flags — movable and selectable, but NOT editable by default
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setZValue(8)  # above wires (6) and components (5)

    # =====================================================================
    # Public properties
    # =====================================================================

    @property
    def instance_id(self) -> str:
        return self._id

    @property
    def source_text(self) -> str:
        """The editable source text (with $..$ math delimiters preserved)."""
        return self._source_text

    @property
    def text_content(self) -> str:
        """The plain text content (source text for editing)."""
        return self._source_text

    @text_content.setter
    def text_content(self, text: str) -> None:
        self._source_text = text
        self.setPlainText(text)
        self._try_render_math()

    @property
    def html_content(self) -> str:
        """The HTML content (for serialization)."""
        return self.toHtml()

    @html_content.setter
    def html_content(self, html: str) -> None:
        self.setHtml(html)

    @property
    def font_family(self) -> str:
        return self.font().family()

    @font_family.setter
    def font_family(self, family: str) -> None:
        f = self.font()
        f.setFamily(family)
        self.setFont(f)
        # Check if font is actually available; warn if fallback is used
        actual = self.font().family()
        if actual.lower() != family.lower() and family:
            _warn_font_fallback(family, actual)
        if _has_any_math(self._source_text):
            self._try_render_math()

    @property
    def font_size(self) -> float:
        return self.font().pointSizeF()

    @font_size.setter
    def font_size(self, size: float) -> None:
        f = self.font()
        f.setPointSizeF(size)
        self.setFont(f)
        if _has_any_math(self._source_text):
            self._try_render_math()

    @property
    def font_bold(self) -> bool:
        return self.font().bold()

    @font_bold.setter
    def font_bold(self, bold: bool) -> None:
        f = self.font()
        f.setBold(bold)
        self.setFont(f)

    @property
    def font_italic(self) -> bool:
        return self.font().italic()

    @font_italic.setter
    def font_italic(self, italic: bool) -> None:
        f = self.font()
        f.setItalic(italic)
        self.setFont(f)

    @property
    def text_color(self) -> QColor:
        return self.defaultTextColor()

    @text_color.setter
    def text_color(self, color: QColor) -> None:
        self.setDefaultTextColor(color)
        if _has_any_math(self._source_text):
            self._try_render_math()

    @property
    def is_editing(self) -> bool:
        return self._editing

    # =====================================================================
    # Math rendering
    # =====================================================================

    def _try_render_math(self) -> None:
        """If source text contains $...$ math, render it as vector SVG.

        Matplotlib handles mixed text+math natively, so we pass the
        entire string (e.g. ``$\\alpha$ hello``) and matplotlib renders
        the math portions as glyphs and the rest as regular text — all
        as vector paths in a single SVG.
        """
        # Never render while the user is editing: the QTextDocument is the
        # live editor buffer, and this method clears it. finish_editing()
        # will re-invoke us once the edit is committed.
        if self._editing:
            return
        text = self._source_text
        if not _has_any_math(text):
            self._math_renderer = None
            self._math_image = None
            self._math_rect = QRectF()
            return

        # If the user typed display math but no real renderer is around,
        # let them know once per session what they need to install. The
        # popup is suppressed if ziamath OR system LaTeX is available, so
        # users with a working pipeline never see it.
        if _has_display_math(text):
            _maybe_warn_no_display_math_backend(parent_widget=None)

        svg_bytes = _render_latex_svg(text, self.font_size, self.text_color,
                                       font_family=self.font_family)
        if svg_bytes is None:
            self._math_renderer = None
            self._math_image = None
            self._math_rect = QRectF()
            return

        renderer = QSvgRenderer(svg_bytes)
        if not renderer.isValid():
            logger.warning("QSvgRenderer says SVG is invalid; first 200 bytes: %s",
                          svg_bytes[:200])
            self._math_renderer = None
            self._math_image = None
            self._math_rect = QRectF()
            return

        self._math_renderer = renderer
        self._math_image = None
        # Print the renderer's actual viewBox + default size so we can
        # tell whether the normalize step landed (origin at 0,0) or not.
        vs = renderer.viewBoxF()
        ds = renderer.defaultSize()
        logger.debug("renderer viewBox=(%s,%s,%s,%s), defaultSize=(%s,%s)",
                     vs.x(), vs.y(), vs.width(), vs.height(), ds.width(), ds.height())
        # _math_rect is the item-local rect where the SVG actually paints
        # (anchored at 0,0 — see paint() for the painter translate trick),
        # so the boundingRect / selection rect line up with the glyphs.
        self._math_rect = QRectF(0, 0, vs.width(), vs.height())
        logger.debug("math_rect set to %s", self._math_rect)

        # Hide the text content (we'll paint the SVG instead)
        self.document().clear()
        self.prepareGeometryChange()

    # =====================================================================
    # Editing
    # =====================================================================

    def start_editing(self) -> None:
        """Enter inline text editing mode."""
        if self._editing:
            return
        self._editing = True

        # Restore source text for editing (replaces rendered math)
        self._math_renderer = None
        self._math_image = None
        self._math_rect = QRectF()
        self.setPlainText(self._source_text)

        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # Select all text for easy replacement
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)
        self.prepareGeometryChange()
        self.update()

    def finish_editing(self) -> None:
        """Exit inline text editing mode."""
        if not self._editing:
            return
        self._editing = False
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # Capture the edited text as new source
        self._source_text = self.toPlainText()

        # Clear selection
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

        # Render math if present
        self._try_render_math()
        self.prepareGeometryChange()
        self.update()

    # =====================================================================
    # Events
    # =====================================================================

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click enters edit mode."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_editing()
            # Let QGraphicsTextItem handle cursor placement
            super().mouseDoubleClickEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        """Escape exits edit mode."""
        if self._editing and event.key() == Qt.Key.Key_Escape:
            self.finish_editing()
            event.accept()
            return
        if self._editing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.finish_editing()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        """Focus lost — finish editing only if the scene deselected us.

        Don't auto-finish here because the properties panel or other
        UI widgets may temporarily steal focus.  Editing is reliably
        ended by Escape, clicking another item (deselection via
        itemChange), or clicking empty canvas.
        """
        super().focusOutEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            if self._skip_snap:
                return value
            from diagrammer.canvas.grid import snap_to_grid
            views = self.scene().views()
            if views and getattr(views[0], '_snap_enabled', True):
                return snap_to_grid(value, views[0].grid_spacing)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if not value and self._editing:
                self.finish_editing()
        return super().itemChange(change, value)

    # =====================================================================
    # Geometry & Painting
    # =====================================================================

    def boundingRect(self) -> QRectF:
        if self._math_renderer is not None:
            return self._math_rect.adjusted(-4, -4, 4, 4)
        return super().boundingRect().adjusted(-2, -2, 2, 2)

    def shape(self) -> QPainterPath:
        """Return the clickable/selectable shape — uses boundingRect for reliable hit-testing."""
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        # Ensure antialiasing is enabled, especially for rotated text on macOS
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._math_renderer is not None:
            # Render the SVG at its native viewBox coordinates and let
            # the painter transform handle the placement. Qt's
            # ``QSvgRenderer.render(painter, target_rect)`` does NOT
            # reliably map a non-zero-origin viewBox into the target rect
            # — empirically, ziamath SVGs with negative-y viewBoxes end
            # up rendered above the target — so we sidestep target_rect
            # entirely. Instead, we translate the painter so that the
            # viewBox's top-left lands at item-local (0, 0), then call
            # render() with the viewBox itself as the target (which is
            # an identity mapping that Qt does honor).
            vs = self._math_renderer.viewBoxF()
            painter.save()
            painter.translate(-vs.x(), -vs.y())
            # Only use the QPicture intermediary when painting onto a
            # QSvgGenerator — it's needed there because QSvgRenderer.render()
            # directly onto a QSvgGenerator painter drops nested SVG content.
            # For all other targets (screen, QPrinter, QImage) render
            # directly; QPicture's built-in resolution scaling (it records
            # at screen DPI and rescales on playback) doubles up with the
            # transform that scene.render() applies, causing math annotations
            # to be massively enlarged in PDF/PNG exports.
            from PySide6.QtSvg import QSvgGenerator
            if isinstance(painter.device(), QSvgGenerator):
                pic = QPicture()
                ppainter = QPainter(pic)
                ppainter.setRenderHint(QPainter.RenderHint.Antialiasing)
                ppainter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
                self._math_renderer.render(ppainter, vs)
                ppainter.end()
                pic.play(painter)
            else:
                self._math_renderer.render(painter, vs)
            painter.restore()
            # Selection highlight (suppressed for grouped items)
            if self.isSelected() and not self._group_id:
                pen = QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH)
                pen.setDashPattern(SELECTION_DASH_PATTERN)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self._math_rect.adjusted(-2, -2, 2, 2))
            return

        # Draw selection highlight for regular text (suppressed for grouped items)
        if self.isSelected() and not self._editing and not self._group_id:
            pen = QPen(SELECTION_PEN_COLOR, SELECTION_PEN_WIDTH)
            pen.setDashPattern(SELECTION_DASH_PATTERN)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(-2, -2, 2, 2))

        # Let QGraphicsTextItem handle text rendering
        super().paint(painter, option, widget)
