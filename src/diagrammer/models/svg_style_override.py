"""Per-instance SVG style overrides for component items.

Each component instance can override style properties on individual SVG
elements (paths, lines, rects, etc.) within the artwork and leads layers.
Overrides are stored as a dict keyed by element path strings like
``artwork/0``, ``leads/1``, etc.

A ``None`` value for any property means "use the original SVG style".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SvgElementStyleOverride:
    """Style overrides for a single SVG element within a component.

    Only non-None fields produce actual SVG attribute changes.
    """
    stroke_color: str | None = None       # hex color, e.g. "#ff0000"
    fill_color: str | None = None         # hex color, "none", or "transparent"
    stroke_width: float | None = None     # in px/pt
    stroke_dasharray: str | None = None   # SVG dash-array, e.g. "5,3"
    stroke_linecap: str | None = None     # "round", "butt", "square"
    opacity: float | None = None          # 0.0 - 1.0

    def to_inline_style(self) -> str:
        """Build an inline CSS style string from non-None fields."""
        parts: list[str] = []
        if self.stroke_color is not None:
            parts.append(f"stroke:{self.stroke_color}")
        if self.fill_color is not None:
            parts.append(f"fill:{self.fill_color}")
        if self.stroke_width is not None:
            parts.append(f"stroke-width:{self.stroke_width}px")
        if self.stroke_dasharray is not None:
            parts.append(f"stroke-dasharray:{self.stroke_dasharray}")
        if self.stroke_linecap is not None:
            parts.append(f"stroke-linecap:{self.stroke_linecap}")
        if self.opacity is not None:
            parts.append(f"opacity:{self.opacity}")
        return ";".join(parts)

    def is_empty(self) -> bool:
        """True if no overrides are set."""
        return all(v is None for v in (
            self.stroke_color, self.fill_color, self.stroke_width,
            self.stroke_dasharray, self.stroke_linecap, self.opacity,
        ))

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict (only non-None fields)."""
        d: dict = {}
        if self.stroke_color is not None:
            d["stroke_color"] = self.stroke_color
        if self.fill_color is not None:
            d["fill_color"] = self.fill_color
        if self.stroke_width is not None:
            d["stroke_width"] = self.stroke_width
        if self.stroke_dasharray is not None:
            d["stroke_dasharray"] = self.stroke_dasharray
        if self.stroke_linecap is not None:
            d["stroke_linecap"] = self.stroke_linecap
        if self.opacity is not None:
            d["opacity"] = self.opacity
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SvgElementStyleOverride:
        """Deserialize from a JSON-compatible dict."""
        return cls(
            stroke_color=d.get("stroke_color"),
            fill_color=d.get("fill_color"),
            stroke_width=d.get("stroke_width"),
            stroke_dasharray=d.get("stroke_dasharray"),
            stroke_linecap=d.get("stroke_linecap"),
            opacity=d.get("opacity"),
        )


@dataclass
class ComponentStyleOverrides:
    """All style overrides for a single component instance.

    Keys are element path strings like ``artwork/0``, ``leads/1``.
    """
    overrides: dict[str, SvgElementStyleOverride] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Return True if no element overrides are set."""
        return not self.overrides or all(v.is_empty() for v in self.overrides.values())

    def get(self, element_path: str) -> SvgElementStyleOverride | None:
        """Return the override for *element_path*, or None if unset."""
        return self.overrides.get(element_path)

    def set(self, element_path: str, override: SvgElementStyleOverride) -> None:
        """Set or remove the override for *element_path*."""
        if override.is_empty():
            self.overrides.pop(element_path, None)
        else:
            self.overrides[element_path] = override

    def clear(self, element_path: str) -> None:
        """Remove the override for *element_path*."""
        self.overrides.pop(element_path, None)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            path: ovr.to_dict()
            for path, ovr in self.overrides.items()
            if not ovr.is_empty()
        }

    @classmethod
    def from_dict(cls, d: dict) -> ComponentStyleOverrides:
        """Deserialize from JSON-compatible dict."""
        return cls(
            overrides={
                path: SvgElementStyleOverride.from_dict(ovr_dict)
                for path, ovr_dict in d.items()
            }
        )

    def __hash__(self) -> int:
        """Hash for use as part of renderer cache keys."""
        items = tuple(sorted(
            (k, v.to_inline_style()) for k, v in self.overrides.items()
            if not v.is_empty()
        ))
        return hash(items)
