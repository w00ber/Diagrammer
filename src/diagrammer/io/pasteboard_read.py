"""Read vector flavours off the macOS pasteboard that Qt cannot see.

Apps that export SVG to the clipboard on macOS tag it with the standard
Uniform Type Identifier ``public.svg-image`` (graphulator, Inkscape, Sketch,
Illustrator all do).  Qt 6 ships no mime converter for that UTI, so
``QClipboard.mimeData()`` reports only ``application/x-qt-image`` — the raster
fallback — and ``hasFormat("image/svg+xml")`` is False even though vector data
is sitting right there.  Qt's own custom-mime flavour
(``com.trolltech.anymime.image--svg+xml``) round-trips only between Qt apps and
cannot be forged from outside, so the fix is to read NSPasteboard directly.

Two paths, mirroring the writer in :mod:`diagrammer.io.exporter`:

1. **ctypes** -- call ``libobjc.dylib``, which ships with every macOS install,
   so this works inside a frozen bundle with no third-party packages.  It does
   need AppKit already loaded in the process for ``NSPasteboard`` to resolve;
   inside the app Qt has loaded it long before any paste.
2. **PyObjC** -- if ``AppKit`` happens to be importable.

Everything here returns ``None`` rather than raising: on a non-macOS platform,
or when the pasteboard holds no SVG, callers just fall through to their
existing behaviour.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

# SVG flavours to look for, most standard first.  Some apps declare the raw
# MIME string as a pasteboard type instead of the UTI, so we check both.
SVG_FLAVOURS = ("public.svg-image", "image/svg+xml")


# -- public API ----------------------------------------------------------

def read_svg() -> bytes | None:
    """Return SVG bytes from the macOS pasteboard, or None if there are none."""
    if sys.platform != "darwin":
        return None

    for reader in (_read_svg_ctypes, _read_svg_pyobjc):
        try:
            data = reader()
        except Exception:
            logger.debug("%s failed, trying next", reader.__name__, exc_info=True)
            continue
        if data:
            return data
    return None


def change_count() -> int | None:
    """Return ``NSPasteboard.changeCount``, or None if it can't be read.

    The count increments on every write by any app, so comparing it across
    time tells us whether the pasteboard's contents are newer than something
    else we remember -- see ``ClipboardMixin._note_internal_copy``.
    """
    if sys.platform != "darwin":
        return None

    for reader in (_change_count_ctypes, _change_count_pyobjc):
        try:
            count = reader()
        except Exception:
            logger.debug("%s failed, trying next", reader.__name__, exc_info=True)
            continue
        if count is not None:
            return count
    return None


# -- macOS: ctypes (no dependencies) ------------------------------------

def _objc_bridge():
    """Return ``(cls, sel, nsstring, send_id, send_id1, send_u64, send_i64)``.

    The ``send_*`` helpers wrap ``objc_msgSend`` cast to the signature each
    selector needs; calling it uncast returns garbage for non-pointer
    returns.
    """
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    msg = objc.objc_msgSend
    msg.restype = ctypes.c_void_p
    msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    def cls(name: str):
        return objc.objc_getClass(name.encode())

    def sel(name: str):
        return objc.sel_registerName(name.encode())

    send_id = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ))
    send_id1 = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ))
    send_u64 = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p,
    ))
    send_i64 = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
    ))
    _send_str = ctypes.cast(msg, ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_uint64,
    ))

    def nsstring(s: str):
        buf = ctypes.create_string_buffer(s.encode("utf-8"))
        # Second arg is ignored by this selector; the cast needs it.
        return _send_str(cls("NSString"), sel("stringWithUTF8String:"), buf, 0)

    return cls, sel, nsstring, send_id, send_id1, send_u64, send_i64


def _read_svg_ctypes() -> bytes | None:
    """Pull SVG bytes off NSPasteboard through the ObjC runtime."""
    import ctypes

    cls, sel, nsstring, send_id, send_id1, send_u64, _ = _objc_bridge()

    NSPasteboard = cls("NSPasteboard")
    if not NSPasteboard:
        # AppKit isn't loaded in this process, so the class doesn't exist.
        raise RuntimeError("NSPasteboard unavailable (AppKit not loaded)")
    pb = send_id(NSPasteboard, sel("generalPasteboard"))
    if not pb:
        raise RuntimeError("Failed to get NSPasteboard")

    for flavour in SVG_FLAVOURS:
        data = send_id1(pb, sel("dataForType:"), nsstring(flavour))
        if not data:
            continue
        length = send_u64(data, sel("length"))
        if not length:
            continue
        ptr = send_id(data, sel("bytes"))
        if not ptr:
            continue
        return ctypes.string_at(ptr, length)
    return None


def _change_count_ctypes() -> int | None:
    cls, sel, _, send_id, _, _, send_i64 = _objc_bridge()

    NSPasteboard = cls("NSPasteboard")
    if not NSPasteboard:
        raise RuntimeError("NSPasteboard unavailable (AppKit not loaded)")
    pb = send_id(NSPasteboard, sel("generalPasteboard"))
    if not pb:
        raise RuntimeError("Failed to get NSPasteboard")
    return send_i64(pb, sel("changeCount"))


# -- macOS: PyObjC -------------------------------------------------------

def _read_svg_pyobjc() -> bytes | None:
    """Same read via AppKit.  Raises ImportError without PyObjC."""
    from AppKit import NSPasteboard

    pb = NSPasteboard.generalPasteboard()
    for flavour in SVG_FLAVOURS:
        data = pb.dataForType_(flavour)
        if data is None:
            continue
        raw = bytes(data)
        if raw:
            return raw
    return None


def _change_count_pyobjc() -> int | None:
    from AppKit import NSPasteboard

    return int(NSPasteboard.generalPasteboard().changeCount())
