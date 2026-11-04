"""The fixes: composable text -> text repairs, each offered as a checkbox.

Two invariants hold the whole preview loop together:

1. **Every fix composes from the pristine original**, never from the candidate
   built so far. ``apply_fixes`` is called fresh on each checkbox toggle. That
   makes unticking a box exact without an inverse transform, and makes the
   result a pure function of the *set* of selected fixes rather than of the
   order they were clicked in.
2. **Application order is deterministic**, by ``(order, fix_id)``. The bands
   matter: structural repairs run before anything that locates elements, and
   geometry-moving ones run last because they rewrite the very coordinates the
   earlier passes found.

Lifted from ``scripts/normalize_editor_svgs.py`` and
``scripts/strip_stray_anchor_points.py``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from diagrammer.svglint import text_svg as T
from diagrammer.svglint.model import (
    ORDER_COSMETIC,
    ORDER_GEOMETRY,
    ORDER_STRUCTURAL,
    ORDER_STYLE,
    Finding,
    Fix,
    FixContext,
    FixResult,
)

_UNCHANGED = ()


def _nop(text: str) -> FixResult:
    return FixResult(text=text, changed=False, notes=_UNCHANGED)


# --------------------------------------------------------------------------
# structural
# --------------------------------------------------------------------------

def _fix_restore_named_ids(text: str, ctx: FixContext) -> FixResult:
    """Move a ``data-name="port:x"`` back into ``id``, dropping the data-name.

    Restores **only** the sanitization pattern — an ``id`` that is exactly the
    ``data-name`` with its colons turned into underscores. That narrowness is
    the whole point, because a second editor behaviour looks identical and must
    not be touched: Illustrator resolves two elements sharing an ``id`` by
    appending a digit to one and recording the original in ``data-name``::

        <circle id="port:2"  .../>                      <!-- port 2 -->
        <circle id="port:21" data-name="port:2" .../>   <!-- a DIFFERENT port -->

    Rewriting the second back to ``port:2`` would duplicate the id and lose a
    port. Here ``"port:2".replace(":", "_")`` is ``port_2``, not ``port:21``, so
    the pattern does not match. As a second guard, a name already used as an
    ``id`` anywhere in the file is never assigned again.
    """
    notes: list[str] = []
    existing = set(re.findall(r'\sid="([^"]*)"', text))

    def fix(m: re.Match[str]) -> str:
        tag = m.group(0)
        dn = re.search(r'\sdata-name="([^"]*)"', tag)
        if dn is None:
            return tag
        name = dn.group(1)
        if not name.startswith(T.NAMED_PREFIXES):
            return tag
        cur = re.search(r'\sid="([^"]*)"', tag)
        if cur is None or cur.group(1) != name.replace(":", "_"):
            return tag
        if name in existing:
            notes.append(f'left id="{cur.group(1)}" alone — "{name}" is '
                         "already used; two elements would collide")
            return tag
        existing.discard(cur.group(1))
        existing.add(name)
        out = tag[:dn.start()] + tag[dn.end():]
        out = re.sub(r'\sid="[^"]*"', f' id="{name}"', out, count=1)
        notes.append(f'restored id="{name}"')
        return out

    new = re.sub(r"<\w+\b[^>]*>", fix, text)
    return FixResult(text=new, changed=new != text, notes=tuple(notes))


def _fix_restore_primitives(text: str, ctx: FixContext) -> FixResult:
    """Turn path-ified ports back into ``<circle>`` and stretch axes into
    ``<line>``, which are the only forms the parser reads."""
    notes: list[str] = []
    original = text

    for group_id, kind in (("ports", "circle"), ("stretch", "line")):
        span = T.group_body(text, group_id)
        if span is None:
            continue
        start, end = span
        body = text[start:end]

        def convert(m: re.Match[str], kind: str = kind) -> str:
            tag = m.group(0)
            dm = re.search(r'\sd="([^"]*)"', tag)
            if dm is None:
                return tag
            attrs = tag[:dm.start()] + tag[dm.end():]
            attrs = re.sub(r"^<path\b", "", attrs)
            attrs = re.sub(r"\s*/?>$", "", attrs).strip()
            attrs = (" " + attrs) if attrs else ""
            if kind == "circle":
                c = T.path_as_circle(dm.group(1))
                if c is None:
                    return tag
                cx, cy, r = c
                notes.append(f"path -> <circle> at ({T.fmt(cx)},{T.fmt(cy)}) "
                             f"r={T.fmt(r)}")
                return (f'<circle{attrs} cx="{T.fmt(cx)}" cy="{T.fmt(cy)}" '
                        f'r="{T.fmt(r)}"/>')
            ln = T.path_as_line(dm.group(1))
            if ln is None:
                return tag
            x1, y1, x2, y2 = ln
            notes.append(f"path -> <line> ({T.fmt(x1)},{T.fmt(y1)})-"
                         f"({T.fmt(x2)},{T.fmt(y2)})")
            return (f'<line{attrs} x1="{T.fmt(x1)}" y1="{T.fmt(y1)}" '
                    f'x2="{T.fmt(x2)}" y2="{T.fmt(y2)}"/>')

        new_body = re.sub(r"<path\b[^>]*/>", convert, body)
        if new_body != body:
            text = text[:start] + new_body + text[end:]

    return FixResult(text=text, changed=text != original, notes=tuple(notes))


def _fix_unwrap_layer_group(text: str, ctx: FixContext) -> FixResult:
    """Hoist the children of a wrapper ``<g id="Layer_1">`` to the root.

    Only unwraps when the wrapper actually contains named layers — a layer
    group that is genuine artwork is left in place.
    """
    m = re.search(r'([ \t]*)<g\s+id="Layer_1"[^>]*>\n?', text)
    if m is None:
        return _nop(text)
    # Match the wrapper's closing tag by depth, so a nested <g> inside artwork
    # does not end it early.
    depth = 1
    pos = m.end()
    close = None
    for tok in re.finditer(r"<g\b[^>]*?(/?)>|</g>", text[pos:]):
        if tok.group(0) == "</g>":
            depth -= 1
            if depth == 0:
                close = (pos + tok.start(), pos + tok.end())
                break
        elif not tok.group(1):
            depth += 1
    if close is None:
        return _nop(text)

    body = text[m.end():close[0]]
    if not re.search(r'<g\s+id="(artwork|ports|leads|labels|stretch)"', body):
        return _nop(text)

    # Pull the hoisted lines back one indent level so the file still reads like
    # the hand-authored components. rstrip at the end rather than strip at the
    # start: the body's first line keeps the indent that dedenting is about to
    # reduce, while its last "line" is the bare indent that sat before the
    # wrapper's </g> and must go entirely, or a blank line is left before
    # </svg>.
    dedented = "\n".join(
        ln[2:] if ln.startswith("  ") else ln
        for ln in body.strip("\n").split("\n")).rstrip()
    tail = text[close[1]:].lstrip("\n")
    new = text[:m.start()] + dedented + "\n" + tail
    return FixResult(text=new, changed=True,
                     notes=('unwrapped <g id="Layer_1">',))


def _fix_wrap_artwork_layer(text: str, ctx: FixContext) -> FixResult:
    """Wrap the unnamed top-level drawing in ``<g id="artwork">``.

    Without that layer the symbol still renders — the parser falls back to
    treating the whole document as artwork — so the fault is invisible on the
    canvas. What it costs is everything that enumerates *named* layers:
    ``enumerate_svg_elements`` and the properties panel walk ``artwork`` and
    the lead groups only, so none of the body's elements can be selected or
    restyled individually. On a real example that was 2 selectable elements
    instead of 8.

    Only the elements that are not already in a named layer are wrapped, and
    they keep their document order, so nothing moves and nothing repaints
    differently.
    """
    if re.search(r'<g\s+id="artwork"', text):
        return _nop(text)
    body = re.search(r"(<svg\b[^>]*>)(.*)(</svg>)", text, re.S)
    if body is None:
        return _nop(text)
    head, inner, tail = body.group(1), body.group(2), body.group(3)

    # Spans to leave alone: defs/metadata, and every already-named layer.
    keep: list[tuple[int, int]] = []
    for pat in (r"<defs\b.*?</defs>", r"<metadata\b.*?</metadata>",
                r"<!--.*?-->", r"<title\b.*?</title>",
                r"<desc\b.*?</desc>"):
        keep += [m.span() for m in re.finditer(pat, inner, re.S)]
    for layer in T.KNOWN_LAYER_IDS:
        span = T.group_body(inner, layer)
        if span is None:
            continue
        open_tag = re.search(rf'<g\s+id="{re.escape(layer)}"[^>]*>', inner)
        start = open_tag.start() if open_tag else span[0]
        close = inner.find("</g>", span[1])
        keep.append((start, close + 4 if close != -1 else span[1]))

    def covered(i: int, j: int) -> bool:
        return any(a <= i and j <= b for a, b in keep)

    # Top-level elements that are not covered are the loose artwork.
    loose: list[tuple[int, int]] = []
    pos = 0
    depth = 0
    for tok in re.finditer(r"<(\w[\w:-]*)\b[^>]*?(/?)>|</(\w[\w:-]*)>", inner):
        if tok.group(3):          # a closing tag
            depth -= 1
            if depth == 0 and not covered(pos, tok.end()):
                loose.append((pos, tok.end()))
            continue
        if depth == 0:
            pos = tok.start()
        if tok.group(2):          # self-closing
            if depth == 0 and not covered(tok.start(), tok.end()):
                loose.append((tok.start(), tok.end()))
            continue
        depth += 1

    if not loose:
        return _nop(text)

    first, last = loose[0][0], loose[-1][1]
    if any(covered(a, b) for a, b in loose):
        return _nop(text)
    # Only wrap a contiguous run; interleaved loose elements and named layers
    # would change paint order, which this fix is not allowed to do.
    if any(a <= first and last <= b for a, b in keep):
        return _nop(text)
    for a, b in keep:
        if first < a < last or first < b < last:
            return _nop(text)

    inner_new = (inner[:first]
                 + '<g id="artwork">'
                 + inner[first:last]
                 + "</g>"
                 + inner[last:])
    n = len(loose)
    return FixResult(text=head + inner_new + tail, changed=True,
                     notes=(f'wrapped {n} top-level element(s) in '
                            f'<g id="artwork">',))


def _fix_strip_editor_metadata(text: str, ctx: FixContext) -> FixResult:
    """Drop the Adobe PGF blob that "Preserve Illustrator Editing
    Capabilities" bakes into an export.

    The ``<metadata>`` block holds ``<i:aipgf>``: the whole document a second
    time as a zstd/base64 private stream. Nothing outside Illustrator reads it —
    Qt's renderer, ``ComponentDef.from_svg`` and every browser skip it — and it
    dwarfs the drawing it accompanies, 45-90 KB against a median component of
    1.5 KB.

    It is **not** reversible: stripping it costs Illustrator's editing state.
    That is the right trade for shipped artwork rather than a working file —
    keep the .ai beside it if the state matters — but it is why this is opt-in.
    """
    if "i:aipgf" not in text:
        return _nop(text)
    before = len(text)
    # Non-greedy + DOTALL: the blob contains a CDATA section, so match to the
    # first </metadata> after it rather than trying to parse the contents.
    new, n = re.subn(r"[ \t]*<metadata>.*?</metadata>\n?", "", text,
                     flags=re.DOTALL)
    if not n:
        return _nop(text)
    # The i: prefix has no other user once the block is gone; leaving the
    # declaration behind would be a dangling namespace.
    if not re.search(r"<i:|\si:[\w-]+=", new):
        new = re.sub(r'\s+xmlns:i="[^"]*"', "", new)
    return FixResult(
        text=new, changed=True,
        notes=(f"stripped Illustrator PGF metadata "
               f"({(before - len(new)) / 1024:.0f} KB)",))


# --------------------------------------------------------------------------
# style, geometry-preserving
# --------------------------------------------------------------------------

def _css_rule_owners(text: str, cls: str, prop: str) -> set[str]:
    """Every class that shares a rule setting ``prop`` with ``cls``.

    Illustrator writes grouped selectors, and they are the reason a rule may
    not be edited in place::

        .st1, .st2, .st3, .st4, .st7, .st5, .st6 { stroke-linecap: round; }

    If ``.st7`` is the lead class and the rest are artwork, rewriting that
    declaration restyles the whole symbol. This is how the fix knows it is
    about to.
    """
    owners: set[str] = set()
    for block in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S):
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", block.group(1)):
            sel, body = rule.group(1), rule.group(2)
            if not re.search(rf"\.{re.escape(cls)}\b", sel):
                continue
            if not re.search(rf"\b{re.escape(prop)}\s*:", body):
                continue
            owners |= {m.group(1) for m in re.finditer(r"\.([\w-]+)", sel)}
    return owners


def _override_css_decl(text: str, classes: Sequence[str], prop: str,
                       value: str) -> tuple[str, list[str]]:
    """Give ``classes`` their own ``prop: value``, by appending one rule.

    Appending rather than editing in place, because Illustrator groups
    selectors: a declaration that happens to mention the lead class usually
    governs half the artwork too, and editing it would restyle the symbol. A
    rule added at the end of the ``<style>`` block has equal specificity and
    later source order, so it wins for exactly the classes named and nothing
    else — verified against Qt's renderer, which honours the cascade here.

    It is also a smaller diff: one added rule instead of an edit scattered
    across every rule the class appears in.
    """
    targets = [c for c in classes
               if (_css_declared(text, c, prop) or "") != value]
    if not targets:
        return text, []
    selector = ", ".join(f".{c}" for c in targets)
    rule = f"\n      {selector} {{\n        {prop}: {value};\n      }}\n    "

    def append(block: re.Match[str]) -> str:
        head, css, tail = block.group(1), block.group(2), block.group(3)
        return f"{head}{css.rstrip()}\n{rule}{tail}"

    new, n = re.subn(r"(<style[^>]*>)(.*?)(</style>)", append, text,
                     count=1, flags=re.S)
    if not n:
        # No <style> block at all: nothing to override, and inventing one
        # would be a bigger change than this fix is allowed to make.
        return text, []
    was = {c: _css_declared(text, c, prop) for c in targets}
    notes = [
        f".{c}: {prop} {was[c]} -> {value}" if was[c]
        else f".{c}: set {prop}: {value}"
        for c in targets
    ]
    return new, notes


def _css_declared(text: str, cls: str, prop: str) -> str | None:
    """The value ``cls`` currently resolves to for ``prop``, or None."""
    return T.css_property(text, prop).get(cls)


def _fix_set_lead_width(text: str, ctx: FixContext) -> FixResult:
    """Set the lead stroke to the library's weight, so the wire matches it."""
    want = ctx.profile.lead_width
    if want is None:
        return _nop(text)
    classes = T.lead_classes(text)
    if not classes:
        return _nop(text)
    # A class shared with the body would restyle the artwork too. The shipped
    # files do share one (.st1 is both body and lead in many parts), so only
    # rewrite when the class is used *exclusively* by leads.
    exclusive = [c for c in classes if not _class_used_outside_leads(text, c)]
    if not exclusive:
        return FixResult(
            text=text, changed=False,
            notes=(f"cannot set the lead width: class(es) "
                   f"{', '.join('.' + c for c in classes)} are shared with the "
                   "artwork, so changing them would restyle the body too. "
                   "Give the leads their own class first.",))
    new, notes = _override_css_decl(text, exclusive, "stroke-width",
                                    f"{T.fmt(want)}px")
    shared = sorted({o for c in exclusive
                     for o in _css_rule_owners(text, c, "stroke-width")}
                    - set(exclusive))
    if shared and new != text:
        notes.append(
            "the existing rule is shared with "
            + ", ".join("." + c for c in shared)
            + ", so a new rule was added for the lead class rather than "
              "editing that one, which would have restyled them too")
    return FixResult(text=new, changed=new != text, notes=tuple(notes))


def _class_used_outside_leads(text: str, cls: str) -> bool:
    """Is ``cls`` used by any element that is not inside a lead layer?"""
    lead_spans: list[tuple[int, int]] = [
        m.span() for m in re.finditer(
            rf'<g id="{T.LEAD_LAYER_RE}"[^>]*>.*?</g>', text, re.S)
    ]
    for m in re.finditer(rf'class="{re.escape(cls)}"', text):
        if not any(a <= m.start() < b for a, b in lead_spans):
            return True
    return False


def _fix_set_lead_linecap(text: str, ctx: FixContext) -> FixResult:
    """Set butt caps on the **lead** classes, and only those.

    A round cap overhangs the endpoint by half the stroke width, so the lead
    reaches past its port and the join with the wire thickens. That is a
    mechanical fault and worth fixing.

    The same cap on *artwork* is a drawing decision — a round-capped stub or
    arrow is often exactly what the author wanted — so it is left alone here
    and offered separately by ``set-artwork-linecap-butt``. Keeping them apart
    matters because Illustrator groups selectors, and the one rule that sets
    the lead's cap usually sets half the symbol's too.
    """
    classes = T.lead_classes(text)
    if not classes:
        return _nop(text)
    exclusive = [c for c in classes if not _class_used_outside_leads(text, c)]
    if not exclusive:
        return FixResult(
            text=text, changed=False,
            notes=(f"cannot set the lead cap: class(es) "
                   f"{', '.join('.' + c for c in classes)} are used by the "
                   "artwork as well, so the change would restyle the body "
                   "too. Give the leads their own class first.",))
    new, notes = _override_css_decl(text, exclusive, "stroke-linecap",
                                    ctx.profile.linecap)
    shared = sorted({o for c in exclusive
                     for o in _css_rule_owners(text, c, "stroke-linecap")}
                    - set(exclusive))
    if shared and new != text:
        notes.append(
            "the existing rule is shared with "
            + ", ".join("." + c for c in shared)
            + ", so a new rule was added for the lead class rather than "
              "editing that one, which would have restyled them too")
    return FixResult(text=new, changed=new != text, notes=tuple(notes))


def _fix_set_artwork_linecap(text: str, ctx: FixContext) -> FixResult:
    """Set butt caps on the artwork classes too.

    Separate, and off by default, because this one is taste rather than
    correctness: unlike a lead, a round-capped artwork stroke breaks nothing.
    It is offered so a library can be made internally consistent in one step
    when that is what the author wants.
    """
    caps = T.css_property(text, "stroke-linecap")
    lead = set(T.lead_classes(text))
    artwork = sorted(c for c, v in caps.items()
                     if v in ("round", "square") and c not in lead)
    if not artwork:
        return _nop(text)
    new, notes = _override_css_decl(text, artwork, "stroke-linecap",
                                    ctx.profile.linecap)
    return FixResult(text=new, changed=new != text, notes=tuple(notes))


# --------------------------------------------------------------------------
# geometry-moving
# --------------------------------------------------------------------------

def _translate(text: str, dx: float, dy: float) -> str:
    """Shift every coordinate in the document by ``(dx, dy)``.

    A rigid transform: ports, leads, artwork, stretch axes and label anchors
    all move together, so every relationship the parser cares about — a lead
    ending on its port, a port sitting on the frame edge — is preserved. The
    viewBox is deliberately left alone, because this undoes an exporter that
    re-based the *content* inside the frame, not one that resized the frame.
    """
    text = re.sub(r'\sd="([^"]*)"',
                  lambda m: f' d="{T.shift_path_data(m.group(1), dx, dy)}"',
                  text)
    for attr, delta in (("cx", dx), ("cy", dy), ("x1", dx), ("x2", dx),
                        ("y1", dy), ("y2", dy), ("x", dx), ("y", dy)):
        text = re.sub(
            rf'\s{attr}="({T.NUM})"',
            lambda m, a=attr, d=delta: f' {a}="{T.fmt(float(m.group(1)) + d)}"',
            text)
    return re.sub(
        r'\sdata-anchor="(' + T.NUM + r")\s*,\s*(" + T.NUM + r')"',
        lambda m: (f' data-anchor="{T.fmt(float(m.group(1)) + dx)},'
                   f'{T.fmt(float(m.group(2)) + dy)}"'),
        text)


def _fix_rebase_viewbox(text: str, ctx: FixContext) -> FixResult:
    """Move the viewBox origin to (0,0), taking the artwork with it.

    ``_assign_approach_directions`` ignores the viewBox origin, so a non-zero
    one makes every port's approach direction wrong. Translating the content by
    ``(-x, -y)`` and zeroing the origin leaves the rendered symbol identical
    while putting the frame back where the parser assumes it is.
    """
    vb = T.viewbox(text)
    if vb is None:
        return _nop(text)
    x, y, w, h = vb
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return _nop(text)
    new = _translate(text, -x, -y)
    new = re.sub(r'viewBox="[^"]*"',
                 f'viewBox="0 0 {T.fmt(w)} {T.fmt(h)}"', new, count=1)
    return FixResult(
        text=new, changed=True,
        notes=(f"viewBox origin ({T.fmt(x)},{T.fmt(y)}) -> (0,0), content "
               f"translated by ({T.fmt(-x)},{T.fmt(-y)})",))


def _fix_snap_lead_end(text: str, ctx: FixContext) -> FixResult:
    """Slide one lead endpoint along its own axis until it meets its port.

    Finding-scoped, so one checkbox repairs every lead in the file that
    qualifies. The check only attaches this fix when the move is a pure
    extension — see ``_is_pure_extension`` — which is why it cannot bend the
    lead or detach its far end. It still moves geometry, so it is never
    default-on: a short lead with a deliberate gap is rare but real, and the
    user should see the change in Overlay mode before saving it.
    """
    f = ctx.finding
    if f is None:
        return _nop(text)
    try:
        ex, ey = float(f.data["end_x"]), float(f.data["end_y"])
        px, py = float(f.data["port_x"]), float(f.data["port_y"])
    except (KeyError, TypeError, ValueError):
        return _nop(text)

    original = text
    notes: list[str] = []
    # Only rewrite inside the lead layers, and only the coordinate pair that
    # matches the reported endpoint, so nothing else in the document moves.
    def in_leads(m: re.Match[str]) -> str:
        body = m.group(0)
        for ax, ay, nx, ny in (("x1", "y1", px, py), ("x2", "y2", px, py)):
            body = re.sub(
                rf'(<line\b[^>]*?)\s{ax}="{re.escape(T.fmt(ex))}"'
                rf'([^>]*?)\s{ay}="{re.escape(T.fmt(ey))}"',
                lambda mm: (f'{mm.group(1)} {ax}="{T.fmt(nx)}"'
                            f'{mm.group(2)} {ay}="{T.fmt(ny)}"'),
                body)
        return body

    text = re.sub(rf'<g id="{T.LEAD_LAYER_RE}"[^>]*>.*?</g>', in_leads,
                  text, flags=re.S)
    if text != original:
        notes.append(f"lead endpoint ({T.fmt(ex)},{T.fmt(ey)}) -> "
                     f"({T.fmt(px)},{T.fmt(py)})")
    else:
        notes.append(f"could not locate the lead endpoint "
                     f"({T.fmt(ex)},{T.fmt(ey)}) as an explicit <line> "
                     "coordinate pair — fix it in the editor")
    return FixResult(text=text, changed=text != original, notes=tuple(notes))


# --------------------------------------------------------------------------
# cosmetic
# --------------------------------------------------------------------------

def _fix_strip_stray_anchors(text: str, ctx: FixContext) -> FixResult:
    """Remove ``<path>`` elements whose ``d`` draws nothing.

    Never touches a path carrying an ``id``: ports, leads and stretch markers
    are addressed by id even when their geometry looks degenerate.
    """
    count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal count
        tag = m.group(0)
        if "id=" in tag:
            return tag
        dm = re.search(r'\bd="([^"]*)"', tag)
        if dm is None:
            return tag
        if any(c in "LlHhVvCcSsQqTtAa" for c in dm.group(1)):
            return tag
        count += 1
        return ""

    new = re.sub(r"[ \t]*<path\b[^>]*/>\n?", sub, text)
    if not count:
        return _nop(text)
    return FixResult(text=new, changed=True,
                     notes=(f"removed {count} stray anchor point(s)",))


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

ALL_FIXES: tuple[Fix, ...] = (
    Fix(fix_id="unwrap-layer-group",
        title='Unwrap the <g id="Layer_1"> wrapper',
        description=("Hoist the named layers to be direct children of <svg>, "
                     "which is what the layer contract requires. Only unwraps "
                     "when the wrapper really holds named layers."),
        order=ORDER_STRUCTURAL, scope="file",
        apply=_fix_unwrap_layer_group, default_on=True, bulk_safe=True),
    Fix(fix_id="restore-named-ids",
        title="Restore port/stretch/label ids sanitized by an editor",
        description=('Move data-name="port:x" back into id. Skips the case '
                     "where Illustrator de-duplicated two ids, which looks "
                     "identical but would lose a port."),
        order=ORDER_STRUCTURAL, scope="file",
        apply=_fix_restore_named_ids, default_on=True, bulk_safe=True),
    Fix(fix_id="restore-primitives",
        title="Convert path-ified ports and stretch axes back to primitives",
        description=("The parser reads <circle>/<rect> for ports and <line> "
                     "for stretch axes only. Converts back any path that is a "
                     "recognisable circle or straight segment."),
        order=ORDER_STRUCTURAL, scope="file",
        apply=_fix_restore_primitives, default_on=True, bulk_safe=True),
    Fix(fix_id="wrap-artwork-layer",
        title='Wrap the loose drawing in <g id="artwork">',
        description=("Without a named artwork layer the symbol still renders, "
                     "but none of its body can be selected or restyled "
                     "individually in the Properties panel, because that "
                     "enumerates named layers only. Elements keep their order, "
                     "so nothing moves."),
        order=ORDER_STRUCTURAL, scope="file",
        apply=_fix_wrap_artwork_layer, default_on=False, bulk_safe=True),
    Fix(fix_id="strip-editor-metadata",
        title="Strip the Illustrator PGF metadata block",
        description=("Removes <i:aipgf>, typically 45-90 KB of private data "
                     "nothing but Illustrator reads. Not reversible: it costs "
                     "Illustrator's own editing state on re-open."),
        order=ORDER_STRUCTURAL, scope="file",
        apply=_fix_strip_editor_metadata, default_on=False, bulk_safe=True),

    Fix(fix_id="set-lead-width",
        title="Set the lead stroke to the library's weight",
        description=("The attached wire takes its width from the lead, so a "
                     "mismatch makes the wire step in thickness at the join. "
                     "Refuses when the lead class is shared with the artwork."),
        order=ORDER_STYLE, scope="file",
        apply=_fix_set_lead_width, default_on=True, bulk_safe=True),
    Fix(fix_id="set-lead-linecap-butt",
        title="Set butt caps on the leads",
        description=("A round or square cap overhangs the endpoint by half the "
                     "stroke width, so the lead reaches past its port and the "
                     "join with the wire thickens. Touches the lead classes "
                     "only — the artwork keeps its caps."),
        order=ORDER_STYLE, scope="file",
        apply=_fix_set_lead_linecap, default_on=False, bulk_safe=True),
    Fix(fix_id="set-artwork-linecap-butt",
        title="Also set butt caps on the artwork",
        description=("House style rather than correctness: a round cap on "
                     "artwork breaks nothing, and on a stub or an arrowhead it "
                     "is usually deliberate. Offered so a library can be made "
                     "consistent in one step when that is what you want."),
        order=ORDER_STYLE, scope="file",
        apply=_fix_set_artwork_linecap, default_on=False, bulk_safe=True),

    Fix(fix_id="rebase-viewbox-to-origin",
        title="Move the viewBox origin to (0,0)",
        description=("Translates the content so the frame starts at the origin, "
                     "which is where _assign_approach_directions assumes it is. "
                     "The rendered symbol is unchanged."),
        order=ORDER_GEOMETRY, scope="file",
        apply=_fix_rebase_viewbox, moves_geometry=True, default_on=False,
        bulk_safe=False),
    Fix(fix_id="snap-lead-end-to-port",
        title="Extend the lead to reach its port",
        description=("Slides the endpoint along the axis the lead already runs "
                     "on until it meets the port, so the far end does not move "
                     "and the lead stays straight. Offered only when that is "
                     "all it would do; a lead not aligned with its port would "
                     "be bent by the move and gets no automatic fix."),
        order=ORDER_GEOMETRY, scope="finding",
        apply=_fix_snap_lead_end, moves_geometry=True, default_on=False,
        bulk_safe=False),

    Fix(fix_id="strip-stray-anchors",
        title="Remove stray anchor points",
        description=("Deletes <path> elements with no drawing command. They are "
                     "invisible in Diagrammer but a vector editor renders each "
                     "as a stray anchor point after an export or paste."),
        order=ORDER_COSMETIC, scope="file",
        apply=_fix_strip_stray_anchors, default_on=True, bulk_safe=True),
)

FIXES_BY_ID: dict[str, Fix] = {f.fix_id: f for f in ALL_FIXES}


def available_fixes(findings: Sequence[Finding]) -> list[Fix]:
    """The fixes these findings can offer, deduped and in application order."""
    ids: list[str] = []
    for f in findings:
        for fid in f.fix_ids:
            if fid not in ids and fid in FIXES_BY_ID:
                ids.append(fid)
    return sorted((FIXES_BY_ID[i] for i in ids),
                  key=lambda f: (f.order, f.fix_id))


def apply_fixes(text: str, fixes: Sequence[Fix], findings: Sequence[Finding],
                *, profile, path) -> tuple[str, list[str]]:
    """Apply ``fixes`` to ``text`` in deterministic order.

    ``text`` must be the pristine original; see the module docstring. Returns
    the candidate and the accumulated notes.

    A ``scope="finding"`` fix runs once per finding that requested it, so one
    checkbox can repair several leads in a file. ``(order, fix_id)`` is a total
    sort, which is what makes the output depend only on the *set* of fixes.
    """
    ctx_base = dict(profile=profile, path=path, original_text=text)
    notes: list[str] = []
    for fix in sorted(set(fixes), key=lambda f: (f.order, f.fix_id)):
        if fix.scope == "finding":
            for finding in findings:
                if fix.fix_id not in finding.fix_ids:
                    continue
                res = fix.apply(text, FixContext(finding=finding, **ctx_base))
                text = res.text
                notes.extend(res.notes)
        else:
            res = fix.apply(text, FixContext(**ctx_base))
            text = res.text
            notes.extend(res.notes)
    return text, notes
