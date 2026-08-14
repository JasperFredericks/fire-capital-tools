"""
Unit tests for tools/site_dd_capture.py and tools/upload_limits.py.

The interesting cases are the ones a filename would get wrong: a HEIC
photo and an MP4 video are both ISO base media files, so anything that
decides "video" from the ftyp box alone classifies every iPhone photo as
a video.
"""

import struct
import tempfile
import unittest
from pathlib import Path

from tools import site_dd_capture as cap
from tools import upload_limits as ul


def write(data: bytes) -> Path:
    p = Path(tempfile.mkdtemp()) / "sample"
    p.write_bytes(data)
    return p


def ftyp(brand: bytes, extra: bytes = b"") -> bytes:
    body = brand + b"\x00\x00\x02\x00" + brand
    return struct.pack(">I", 8 + len(body)) + b"ftyp" + body + extra


class SniffTests(unittest.TestCase):
    def test_plain_photo_formats(self):
        cases = [
            (b"\xff\xd8\xff\xe0" + b"0" * 20, "image/jpeg", ".jpg"),
            (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png", ".png"),
            (b"GIF89a" + b"0" * 20, "image/gif", ".gif"),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 12, "image/webp", ".webp"),
        ]
        for data, mime, ext in cases:
            with self.subTest(mime=mime):
                info = cap.sniff(write(data))
                self.assertEqual(info["kind"], cap.KIND_PHOTO)
                self.assertEqual(info["mime"], mime)
                self.assertEqual(info["ext"], ext)

    def test_heic_is_a_photo_not_a_video(self):
        """The trap. HEIC is an ISO base media file exactly like MP4, so a
        check that stops at 'ftyp' calls every iPhone photo a video."""
        for brand in (b"heic", b"heix", b"mif1", b"avif"):
            with self.subTest(brand=brand):
                info = cap.sniff(write(ftyp(brand) + b"0" * 40))
                self.assertEqual(info["kind"], cap.KIND_PHOTO,
                                 f"{brand!r} is a still image container")

    def test_mp4_and_mov_are_videos(self):
        info = cap.sniff(write(ftyp(b"isom") + b"0" * 40))
        self.assertEqual(info["kind"], cap.KIND_VIDEO)
        self.assertEqual(info["ext"], ".mp4")
        info = cap.sniff(write(ftyp(b"qt  ") + b"0" * 40))
        self.assertEqual(info["kind"], cap.KIND_VIDEO)
        self.assertEqual(info["mime"], "video/quicktime")
        self.assertEqual(info["ext"], ".mov", "iOS records .mov")

    def test_the_extension_is_never_trusted(self):
        """A file named .jpg whose content is an MP4 is a video."""
        p = Path(tempfile.mkdtemp()) / "definitely_a_photo.jpg"
        p.write_bytes(ftyp(b"mp42") + b"0" * 40)
        self.assertEqual(cap.sniff(p)["kind"], cap.KIND_VIDEO)

        p2 = Path(tempfile.mkdtemp()) / "clip.mp4"
        p2.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 30)
        self.assertEqual(cap.sniff(p2)["kind"], cap.KIND_PHOTO)

    def test_unknown_content_is_refused_not_guessed(self):
        for data in (b"%PDF-1.4" + b"0" * 30,
                     b"PK\x03\x04" + b"0" * 30,
                     b"just some text, honestly" * 3,
                     ftyp(b"zzzz") + b"0" * 30):
            with self.subTest(data=data[:8]):
                with self.assertRaises(cap.UnsupportedMedia):
                    cap.sniff(write(data))

    def test_a_truncated_file_is_refused(self):
        with self.assertRaises(cap.UnsupportedMedia):
            cap.sniff(write(b"\xff\xd8"))


class ProbeTests(unittest.TestCase):
    """Duration and resolution without ffmpeg, by walking the box tree."""

    def _mp4(self, duration_units=900, timescale=30, width=1280, height=720,
             moov_last=False):
        mvhd_body = (b"\x00\x00\x00\x00"                    # version 0 + flags
                     + b"\x00" * 8                          # created/modified
                     + struct.pack(">I", timescale)
                     + struct.pack(">I", duration_units)
                     + b"\x00" * 80)
        mvhd = struct.pack(">I", 8 + len(mvhd_body)) + b"mvhd" + mvhd_body

        tkhd_body = (b"\x00\x00\x00\x07" + b"\x00" * 76
                     + struct.pack(">II", width << 16, height << 16))
        tkhd = struct.pack(">I", 8 + len(tkhd_body)) + b"tkhd" + tkhd_body
        trak = struct.pack(">I", 8 + len(tkhd)) + b"trak" + tkhd

        moov_body = mvhd + trak
        moov = struct.pack(">I", 8 + len(moov_body)) + b"moov" + moov_body
        mdat_body = b"\x00" * 64
        mdat = struct.pack(">I", 8 + len(mdat_body)) + b"mdat" + mdat_body
        head = ftyp(b"isom")
        return head + (mdat + moov if moov_last else moov + mdat)

    def test_duration_is_read(self):
        p = write(self._mp4(duration_units=900, timescale=30))
        probe = cap.probe_video(p)
        self.assertAlmostEqual(probe["duration_s"], 30.0, places=6)
        self.assertTrue(probe["probed"])

    def test_resolution_is_read(self):
        probe = cap.probe_video(write(self._mp4(width=1280, height=720)))
        self.assertEqual((probe["width"], probe["height"]), (1280, 720))

    def test_moov_after_mdat_still_parses(self):
        """iOS often writes the movie header last. Scanning for the box
        wherever it is means the probe still works."""
        probe = cap.probe_video(write(self._mp4(moov_last=True)))
        self.assertAlmostEqual(probe["duration_s"], 30.0, places=6)
        self.assertEqual(probe["height"], 720)

    def test_an_unparseable_file_returns_none_not_a_guess(self):
        probe = cap.probe_video(write(b"\xff\xd8\xff" + b"0" * 100))
        self.assertIsNone(probe["duration_s"])
        self.assertIsNone(probe["height"])
        self.assertFalse(probe["probed"])

    def test_probe_never_raises(self):
        for data in (b"", b"\x00" * 4, ftyp(b"isom"),
                     struct.pack(">I", 999999) + b"moov" + b"\x00" * 4):
            with self.subTest(data=data[:8]):
                cap.probe_video(write(data))   # must not raise


class LimitTests(unittest.TestCase):
    def test_photo_and_video_size_limits(self):
        cap.check_size(cap.KIND_PHOTO, cap.MAX_PHOTO_BYTES)
        with self.assertRaises(cap.MediaTooLarge):
            cap.check_size(cap.KIND_PHOTO, cap.MAX_PHOTO_BYTES + 1)
        cap.check_size(cap.KIND_VIDEO, cap.MAX_VIDEO_BYTES)
        with self.assertRaises(cap.MediaTooLarge):
            cap.check_size(cap.KIND_VIDEO, cap.MAX_VIDEO_BYTES + 1)

    def test_an_over_long_video_is_refused_on_duration(self):
        p = write(ProbeTests()._mp4(duration_units=60 * 30, timescale=30))
        with self.assertRaises(cap.MediaTooLarge) as ctx:
            cap.check_video(p, 1024)
        self.assertIn("seconds", str(ctx.exception))

    def test_a_30s_video_is_accepted(self):
        p = write(ProbeTests()._mp4(duration_units=30 * 30, timescale=30))
        probe = cap.check_video(p, 1024)
        self.assertAlmostEqual(probe["duration_s"], 30.0, places=6)

    def test_slightly_over_30s_is_tolerated(self):
        """A clip the camera stopped at 31s is not a policy violation."""
        p = write(ProbeTests()._mp4(duration_units=31 * 30, timescale=30))
        cap.check_video(p, 1024)

    def test_an_over_tall_video_is_refused_on_resolution(self):
        p = write(ProbeTests()._mp4(width=3840, height=2160))
        with self.assertRaises(cap.MediaTooLarge) as ctx:
            cap.check_video(p, 1024)
        self.assertIn("720p", str(ctx.exception))

    def test_720p_and_near_720p_are_accepted(self):
        for h in (720, 750, 800):
            with self.subTest(height=h):
                cap.check_video(write(ProbeTests()._mp4(height=h)), 1024)

    def test_size_still_bounds_an_unprobeable_video(self):
        """The honest fallback: when the box tree cannot be read, duration
        and resolution are unknown and size is the whole limit."""
        p = write(b"\x00" * 32)
        with self.assertRaises(cap.MediaTooLarge):
            cap.check_video(p, cap.MAX_VIDEO_BYTES + 1)

    def test_one_video_per_finding_is_the_documented_rule(self):
        self.assertEqual(cap.MAX_VIDEOS_PER_FINDING, 1)


class UploadLimitTests(unittest.TestCase):
    def test_global_cap_matches_config(self):
        from config import Config
        self.assertEqual(Config.MAX_CONTENT_LENGTH, ul.GLOBAL_MAX_CONTENT_BYTES)

    def test_global_cap_leaves_room_for_the_largest_upload(self):
        self.assertGreater(ul.GLOBAL_MAX_CONTENT_BYTES, cap.MAX_VIDEO_BYTES,
                           "the backstop must exceed the largest thing allowed")

    def test_capture_limits_agree_between_modules(self):
        self.assertEqual(ul.VIDEO_BYTES, cap.MAX_VIDEO_BYTES)
        self.assertEqual(ul.PHOTO_BYTES, cap.MAX_PHOTO_BYTES)

    def test_spreadsheet_endpoints_keep_their_old_20mb_protection(self):
        """The point of the per-endpoint table: raising the global cap for
        video must not hand every other endpoint 48 MB by accident."""
        self.assertEqual(ul.SPREADSHEET_BYTES, 20 * 1024 * 1024)
        for endpoint in ("scorecard_pro.upload", "mmr.upload",
                         "underwriting.upload_rentroll", "underwriting.upload_t12",
                         "deal_analyzer.index"):
            self.assertEqual(ul.ENDPOINT_LIMITS[endpoint], 20 * 1024 * 1024,
                             f"{endpoint} must keep its 20 MB limit")

    def test_every_upload_endpoint_is_in_the_table(self):
        """A new upload route must not quietly inherit the 48 MB global cap
        as its only protection, which is the state this whole file exists
        to end."""
        import re
        from pathlib import Path as P
        root = P(__file__).resolve().parents[1] / "tools"
        found = set()
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "request.files" in text:
                found.add(path.name)
        expected = {"site_dd.py", "deal_dive.py", "deal_analyzer.py",
                    "mmr_summary.py", "underwriting.py", "routes.py"}
        self.assertTrue(expected <= found, f"unexpected upload sites: {found}")
        # Each of those modules must reference the limits module.
        for name in expected:
            matches = list(root.rglob(name))
            self.assertTrue(
                any("upload_limits" in m.read_text(encoding="utf-8", errors="replace")
                    for m in matches),
                f"{name} accepts uploads but does not use upload_limits")

    def test_check_refuses_over_the_limit_and_allows_unknown_length(self):
        ul.check(10, 100)
        ul.check(None, 100)          # no Content-Length: the global cap still applies
        with self.assertRaises(ul.UploadTooLarge):
            ul.check(101, 100)


class HumanBytesTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(cap.human_bytes(0), "0 B")
        self.assertEqual(cap.human_bytes(1536), "1.5 KB")
        self.assertEqual(cap.human_bytes(40 * 1024 * 1024), "40.0 MB")
        self.assertEqual(cap.human_bytes(None), "0 B")
        self.assertEqual(cap.human_bytes("nonsense"), "—")


if __name__ == "__main__":
    unittest.main()
