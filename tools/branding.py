"""
FIRE Capital Tools - Brand asset locations.

One place that knows where the logo lives, so PDF exports, templates and
anything added later all resolve the same file. Before this, the path was
spelled out twice (scorecard_pro/exports.py and site_dd.py), which is the
shape of thing that quietly diverges the moment one of them is updated.

Assets under static/img/:

  logo-full.png  Full-colour horizontal lockup, flame + "FIRE Capital"
                 wordmark. The wordmark is navy (#0E3386), so this is for
                 light backgrounds only -- PDF pages, print, white cards.
  logo-mark.svg  Icon-only flame, vector, full opacity.
  logo-mark.png  The same flame rasterised and padded square, for the
                 favicon fallback and anywhere a raster is easier.

There is deliberately no white/reversed lockup: the supplied artwork does
not include one, and the navy wordmark measures 1.30:1 against the
sidebar's #1a2744, which is unreadable. The sidebar therefore pairs the
flame mark (4.2-10:1 on that background) with live text rather than
showing a wordmark nobody can see.
"""

from __future__ import annotations

from pathlib import Path

LOGO_FULL = "img/logo-full.png"
LOGO_MARK_SVG = "img/logo-mark.svg"
LOGO_MARK_PNG = "img/logo-mark.png"

# Kept as a fallback so an older deployment, or a checkout where the new
# asset is missing, still renders a logo instead of a blank header.
LEGACY_LOGO = "fire_logo.png"


def logo_png_path(static_root) -> Path | None:
    """Absolute path to the logo to print on a light background, or None if
    no asset is present. Takes the static root as an argument rather than
    reading flask.current_app, so PDF builders can stay free of Flask
    imports and remain testable with no application context."""
    root = Path(static_root)
    for candidate in (LOGO_FULL, LEGACY_LOGO):
        path = root / candidate
        if path.exists():
            return path
    return None
