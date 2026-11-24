"""Per-instance lead override: one placed part's lead at one port (#47).

When a part's lead disagrees with the wire it continues, the fix is not
always the wire or a swap: sometimes this one placed part should simply draw
that lead at another width or colour. The override is the instance's, never
the library file's; it is saved in the ``.dgm``, travels with copy, paste
and duplicate, and is shown and reversed in Properties.

It is stored as ordinary per-element style overrides
(``ComponentItem.style_overrides``) on the lead elements that end at the
port — ``PortDef.lead_paths``, worked out when the part is parsed. That is
the machinery isolation-mode restyling already uses, so the renderer, the
serializer, the clipboard and duplicate all carry it with nothing new; what
this module adds is the port-shaped view of it, and
:func:`~diagrammer.commands.connect_command.lead_style_at` reads it back so
the wiring checks and new wires see the lead as drawn.

A lead element that ends at two ports (a through line) is shared, so an
override at one of those ports shows at both.
"""

from __future__ import annotations

#: Override attribute -> ``SvgElementStyleOverride`` field.
_FIELDS = {"width": "stroke_width", "color": "stroke_color"}


def lead_paths(item, port_name: str) -> tuple[str, ...]:
    """The element paths of the lead at *port_name* on *item*."""
    port = item.port_by_name(port_name) if hasattr(item, "port_by_name") else None
    pdef = getattr(port, "port_def", None)
    return tuple(getattr(pdef, "lead_paths", ()) or ())


def lead_override_at(item, port_name: str) -> dict:
    """``{"width": w, "color": "#rrggbb"}`` overridden on this instance at
    *port_name*, only the keys that are set. ``{}`` for none."""
    overrides = getattr(item, "style_overrides", None)
    if overrides is None or overrides.is_empty():
        return {}
    from diagrammer.models.lead_style import normalize_color
    out: dict = {}
    for path in lead_paths(item, port_name):
        ovr = overrides.get(path)
        if ovr is None:
            continue
        if "width" not in out and ovr.stroke_width is not None:
            out["width"] = float(ovr.stroke_width)
        if "color" not in out and ovr.stroke_color is not None:
            c = normalize_color(ovr.stroke_color)
            if c is not None:
                out["color"] = c
    return out


def can_override(item, port_name: str) -> bool:
    """Whether a lead ends at *port_name*, so there is something to set."""
    return bool(lead_paths(item, port_name))


def set_lead_override(stack, item, port_name: str, text: str | None = None,
                      **attrs) -> bool:
    """Set ``width=`` and/or ``color=`` on the lead at *port_name*, as one
    undo step. ``None`` for an attribute clears it. False when no lead ends
    at the port."""
    from diagrammer.commands.svg_style_command import ChangeSvgStyleCommand
    paths = lead_paths(item, port_name)
    if not paths:
        return False
    unknown = set(attrs) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unknown lead attribute(s): {sorted(unknown)}")
    stack.beginMacro(text or f"Lead override on {item.component_def.name}:"
                             f"{port_name}")
    try:
        for attr, value in attrs.items():
            prop = _FIELDS[attr]
            if attr == "width" and value is not None:
                value = float(value)
            for path in paths:
                old = item.get_element_style(path, prop)
                if old != value:
                    stack.push(ChangeSvgStyleCommand(item, path, prop, old, value))
    finally:
        stack.endMacro()
    scene = item.scene()
    if scene is not None and hasattr(scene, "update_connections"):
        scene.update_connections()
    return True


def clear_lead_override(stack, item, port_name: str) -> bool:
    """Back to the library's lead at *port_name*, as one undo step."""
    return set_lead_override(
        stack, item, port_name,
        text=f"Reset lead on {item.component_def.name}:{port_name}",
        width=None, color=None)
