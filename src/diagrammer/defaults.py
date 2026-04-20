"""Factory defaults loader.

Reads ``defaults.yaml`` (shipped with the app) to define all original
default values.  The Settings dialog's "Reset to Original Defaults"
button uses these values.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULTS_PATH = Path(__file__).parent / "defaults.yaml"
_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    try:
        import yaml
        _CACHE = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except ImportError:
        # Fallback: simple YAML-subset parser for flat key-value pairs
        _CACHE = _parse_simple_yaml(_DEFAULTS_PATH)
    except (OSError, ValueError) as exc:
        logger.debug("Failed to load defaults.yaml: %s", exc)
        _CACHE = {}
    return _CACHE


def _parse_simple_yaml(path: Path) -> dict:
    """Minimal YAML parser that handles our nested key: value structure."""
    result: dict = {}
    current_section: dict | None = None
    section_name = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level section (no leading whitespace)
        if not line[0].isspace() and stripped.endswith(":"):
            section_name = stripped[:-1]
            current_section = {}
            result[section_name] = current_section
            continue

        # Key-value within a section
        if current_section is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            # Strip inline comments
            if "#" in val:
                val = val[:val.index("#")].strip()
            # Strip quotes
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            # Type conversion
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
                try:
                    val = float(val)
                    if val == int(val):
                        val = int(val)
                except (ValueError, OverflowError):
                    pass
            current_section[key] = val

    return result


def get(section: str, key: str, fallback: object = None) -> object:
    """Look up a factory default value."""
    data = _load()
    sec = data.get(section, {})
    return sec.get(key, fallback)


def get_section(section: str) -> dict:
    """Return all defaults for a section."""
    return dict(_load().get(section, {}))


def all_defaults() -> dict:
    """Return the entire defaults dict."""
    return dict(_load())
