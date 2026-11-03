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

def _rewrite_css_decl(text: str, classes: Sequence[str], prop: str,
                      value: str) -> tuple[str, list[str]]:
    """Set ``prop: value`` on each of ``classes`` inside the ``<style>`` block.

    Edits the declaration in place when the class already sets the property,
    and appends it to the rule otherwise, so the file keeps its formatting and
    the diff shows one line per real change.
    """
    notes: list[str] = []

    def in_block(block: re.Match[str]) -> str:
        css = block.group(1)

        def in_rule(rule: re.Match[str]) -> str:
            sel, body = rule.group(1), rule.group(2)
            hit = [c for c in classes if re.search(rf"\.{re.escape(c)}\b", sel)]
            if not hit:
                return rule.group(0)
            existing = re.search(rf"(\b{re.escape(prop)}\s*:\s*)([^;}}]+)", body)
            if existing:
                if existing.group(2).strip() == value:
                    return rule.group(0)
                notes.append(f".{hit[0]}: {prop} "
                             f"{existing.group(2).strip()} -> {value}")
                body = body[:existing.start(2)] + value + body[existing.end(2):]
            else:
                notes.append(f".{hit[0]}: added {prop}: {value}")
                indent = re.match(r"\s*", body).group(0)
                body = body.rstrip() + f"{indent}{prop}: {value};" + "\n      "
            return f"{sel}{{{body}}}"

        return block.group(0).replace(css, re.sub(r"([^{}]+)\{([^}]*)\}",
                                                  in_rule, css))

    new = re.sub(r"<style[^>]*>(.*?)</style>", in_block, text, flags=re.S)
    return new, notes


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
    new, notes = _rewrite_css_decl(text, exclusive, "stroke-width",
                                   f"{T.fmt(want)}px")
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
    """Set butt caps on the lead classes, so a lead stops at its port."""
    classes = T.lead_classes(text)
    if not classes:
        return _nop(text)
    exclusive = [c for c in classes if not _class_used_outside_leads(text, c)]
    targets = exclusive or classes
    new, notes = _rewrite_css_decl(text, targets, "stroke-linecap",
                                   ctx.profile.linecap)
    if exclusive != classes and new != text:
        notes.append("note: the lead class is shared with the artwork, so the "
                     "cap change applies to both — check the body in Overlay "
                     "mode before saving")
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
                     "stroke width, so the lead reaches past its port."),
        order=ORDER_STYLE, scope="file",
        apply=_fix_set_lead_linecap, default_on=False, bulk_safe=True),

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
