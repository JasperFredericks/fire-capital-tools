"""
FIRE Capital Tools - Site DD photo and video capture rules.

What a phone is allowed to attach to a finding, and how the server checks
it. Pure: no Flask, no database, no network. Reads bytes from a path when
asked to probe a file, and nothing else.

WHY CONTENT IS SNIFFED AND THE EXTENSION IS IGNORED

iOS records .mov, Android records .mp4, and both are H.264 in an ISO base
media container. A filename is whatever the client says it is, so the
extension is treated as a hint for display and never as the answer.

The trap that makes this more than pedantry: HEIC photos are ALSO ISO
base media files. A naive "does it start with ftyp -> it's a video" check
classifies every iPhone photo as a video. The brand field inside the ftyp
box is what separates them, so that is what this module reads.

WHY DURATION AND RESOLUTION ARE CHECKED HERE AND NOT ASSUMED UNCHECKABLE

The Phase 1 investigation concluded that duration and resolution could
not be verified server-side because ffmpeg, cv2 and moviepy are all
absent -- which is true, and led to the plan of using file size as the
only proxy. That plan understated what is possible.

MP4 and MOV are box-structured formats. The movie header box (mvhd)
carries a timescale and a duration, and each track header (tkhd) carries
the track's display width and height as 16.16 fixed-point numbers. Both
are readable with struct and a loop. So duration and resolution are
enforced directly, not inferred from bytes.

Size remains a limit too, because a probe can fail -- an unusual encoder,
a fragmented file with no top-level mvhd -- and a file whose duration
cannot be established must still not be unbounded. When the probe cannot
answer, the size limit is the whole answer and the finding records
duration_s as NULL rather than a guess.

THE LIMITS EXIST BECAUSE OF A 4.6 GB VOLUME

Not for tidiness. At 40 MB a video, the entire production volume holds
about 115 of them. Video is the exception for something a still cannot
convey, photos are the default, and one video per finding is the rule.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

KIND_PHOTO = "photo"
KIND_VIDEO = "video"

# ── Limits ───────────────────────────────────────────────────────────────
#
# 30 seconds at 720p is roughly 25-40 MB depending on the encoder's
# bitrate, so 40 MB accepts a normal capture and rejects an unmodified
# 1080p/4K one. These are the real enforcement points: with no transcoder
# available, the app cannot shrink an oversized file, only refuse it.
MAX_PHOTO_BYTES = 12 * 1024 * 1024        # 12 MB
MAX_VIDEO_BYTES = 40 * 1024 * 1024        # 40 MB
MAX_VIDEO_SECONDS = 30.0
MAX_VIDEO_HEIGHT = 720
# Tolerance on the height check: a phone that records 736 or 750 lines is
# still "720p" in every sense that matters for storage, and rejecting it
# on a technicality would be indistinguishable from a bug to the person
# holding the phone.
VIDEO_HEIGHT_TOLERANCE = 0.12
# Duration is compared with a little slack for the same reason -- a clip
# the camera stopped at 30.4s is not a policy violation.
VIDEO_SECONDS_TOLERANCE = 2.0

MAX_VIDEOS_PER_FINDING = 1

# ── Signatures ───────────────────────────────────────────────────────────

# Plain magic numbers, checked at offset 0.
_SIMPLE_PHOTO_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)

# ISO base media brands. Both photos and videos live in this container
# family, which is exactly why the brand has to be read.
_PHOTO_BRANDS = {
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs",
    b"mif1", b"msf1", b"avif", b"avis",
}
_VIDEO_BRANDS = {
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"mp71",
    b"avc1", b"qt  ", b"M4V ", b"M4A ", b"mmp4", b"3gp4", b"3gp5", b"3g2a",
    b"dash", b"MSNV", b"NDSC", b"NDSM",
}

_BRAND_MIME = {
    b"qt  ": "video/quicktime",
}


class UnsupportedMedia(Exception):
    """The file is not something this tool accepts. The message is written
    to be shown to the person who just tried to upload it."""


class MediaTooLarge(Exception):
    pass


def _read_head(path, n: int = 32) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def sniff(path) -> dict[str, Any]:
    """Identify a file by its content.

    Returns {"kind", "mime", "ext", "brand"}. Raises UnsupportedMedia for
    anything not recognised, rather than guessing -- an unknown file
    stored as a photo would render as a broken image later, which is a
    worse outcome than a clear refusal now.
    """
    head = _read_head(path, 32)
    if len(head) < 12:
        raise UnsupportedMedia("That file is too small to be a photo or video.")

    for sig, mime, ext in _SIMPLE_PHOTO_SIGNATURES:
        if head.startswith(sig):
            return {"kind": KIND_PHOTO, "mime": mime, "ext": ext, "brand": None}

    # RIFF....WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return {"kind": KIND_PHOTO, "mime": "image/webp", "ext": ".webp", "brand": None}

    # ISO base media: [4-byte size][ftyp][4-byte brand]
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _PHOTO_BRANDS:
            return {"kind": KIND_PHOTO, "mime": "image/heic", "ext": ".heic",
                    "brand": brand.decode("ascii", "replace").strip()}
        if brand in _VIDEO_BRANDS:
            mime = _BRAND_MIME.get(brand, "video/mp4")
            ext = ".mov" if mime == "video/quicktime" else ".mp4"
            return {"kind": KIND_VIDEO, "mime": mime, "ext": ext,
                    "brand": brand.decode("ascii", "replace").strip()}
        raise UnsupportedMedia(
            f"That looks like a media file this tool doesn't recognise "
            f"(type '{brand.decode('ascii', 'replace').strip()}'). "
            f"Photos and H.264 video only.")

    raise UnsupportedMedia(
        "That file isn't a photo or a video this tool can read. "
        "JPEG, PNG, HEIC and WebP photos, or MP4/MOV video.")


# ── ISO base media probing ───────────────────────────────────────────────

def _iter_boxes(fh, end: int, start: int = 0):
    """Walk the boxes at one level. Yields (type, payload_start, payload_end).

    Tolerant by design: a malformed or zero-length box ends the walk
    rather than raising, because the caller's fallback (size-only
    enforcement) is a safe answer and a traceback is not.
    """
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return
        size = struct.unpack(">I", header[:4])[0]
        btype = header[4:8]
        payload = pos + 8
        if size == 1:                      # 64-bit extended size
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            payload = pos + 16
        elif size == 0:                    # runs to end of file
            size = end - pos
        if size < 8 or pos + size > end:
            return
        yield btype, payload, pos + size
        pos += size


def _find_box(fh, path_types: tuple[bytes, ...], start: int, end: int):
    """Depth-first search for a nested box, e.g. (moov, mvhd)."""
    head, rest = path_types[0], path_types[1:]
    for btype, pstart, pend in _iter_boxes(fh, end, start):
        if btype == head:
            if not rest:
                return pstart, pend
            found = _find_box(fh, rest, pstart, pend)
            if found:
                return found
    return None


def probe_video(path) -> dict[str, Any]:
    """Duration and display dimensions from an MP4/MOV, without a library.

    Never raises. Returns duration_s/width/height as None when the file
    cannot be parsed, which the caller treats as "unverified" and falls
    back to the size limit for -- an honest gap rather than a fabricated
    figure.
    """
    result = {"duration_s": None, "width": None, "height": None, "probed": False}
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as fh:
            mvhd = _find_box(fh, (b"moov", b"mvhd"), 0, size)
            if mvhd:
                start, _ = mvhd
                fh.seek(start)
                version = fh.read(1)[0]
                fh.seek(start + 4)          # skip version+flags
                if version == 1:
                    fh.read(16)             # creation + modification (64-bit)
                    timescale = struct.unpack(">I", fh.read(4))[0]
                    duration = struct.unpack(">Q", fh.read(8))[0]
                else:
                    fh.read(8)              # creation + modification (32-bit)
                    timescale = struct.unpack(">I", fh.read(4))[0]
                    duration = struct.unpack(">I", fh.read(4))[0]
                if timescale:
                    result["duration_s"] = duration / timescale
                    result["probed"] = True

            # Largest track dimensions: the video track, not a metadata one.
            best = (0, 0)
            moov = _find_box(fh, (b"moov",), 0, size)
            if moov:
                mstart, mend = moov
                for btype, tstart, tend in _iter_boxes(fh, mend, mstart):
                    if btype != b"trak":
                        continue
                    tkhd = _find_box(fh, (b"tkhd",), tstart, tend)
                    if not tkhd:
                        continue
                    kstart, _ = tkhd
                    fh.seek(kstart)
                    version = fh.read(1)[0]
                    # width/height are the last 8 bytes of tkhd, as 16.16
                    offset = 92 if version == 1 else 80
                    fh.seek(kstart + offset)
                    raw = fh.read(8)
                    if len(raw) == 8:
                        w, h = struct.unpack(">II", raw)
                        w, h = w >> 16, h >> 16
                        if w * h > best[0] * best[1]:
                            best = (w, h)
            if best != (0, 0):
                result["width"], result["height"] = best
                result["probed"] = True
    except Exception:
        # A probe is best-effort. Anything unreadable leaves the fields
        # None and the size limit doing the work.
        return result
    return result


# ── Enforcement ──────────────────────────────────────────────────────────

def check_size(kind: str, size_bytes: int) -> None:
    limit = MAX_VIDEO_BYTES if kind == KIND_VIDEO else MAX_PHOTO_BYTES
    if size_bytes > limit:
        raise MediaTooLarge(
            f"That {kind} is {size_bytes / 1024 / 1024:.1f} MB — the limit is "
            f"{limit // 1024 // 1024} MB. "
            + ("Record a shorter clip, or drop the camera to 720p."
               if kind == KIND_VIDEO else "Try a smaller image."))


def check_video(path, size_bytes: int) -> dict[str, Any]:
    """Every video rule, in one place. Raises MediaTooLarge with a message
    for the user, or returns the probe result to be stored."""
    check_size(KIND_VIDEO, size_bytes)
    probe = probe_video(path)

    duration = probe.get("duration_s")
    if duration is not None and duration > MAX_VIDEO_SECONDS + VIDEO_SECONDS_TOLERANCE:
        raise MediaTooLarge(
            f"That clip is {duration:.0f} seconds — the limit is "
            f"{MAX_VIDEO_SECONDS:.0f}. Record a shorter one showing just the issue.")

    height = probe.get("height")
    if height and height > MAX_VIDEO_HEIGHT * (1 + VIDEO_HEIGHT_TOLERANCE):
        raise MediaTooLarge(
            f"That video is {probe.get('width')}x{height} — the limit is "
            f"{MAX_VIDEO_HEIGHT}p. Set the camera to 720p and record it again.")
    return probe


# The stored extension is content-derived (sniff() renames to match what
# the bytes actually are), so it is the trustworthy source for a served
# Content-Type. The ORIGINAL filename is not: a video the phone called
# .mov but encoded as MP4 would otherwise be served as video/quicktime
# purely because of what the client typed.
_EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
}


def mime_for_stored_name(stored_name: Any) -> str | None:
    ext = Path(str(stored_name or "")).suffix.lower()
    return _EXT_MIME.get(ext)


def human_bytes(n: Any) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"
