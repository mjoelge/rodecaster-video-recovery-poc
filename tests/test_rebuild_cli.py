"""Command-level reconstruction checks using only generated ISO BMFF bytes."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rvc_poc import rebuild

from fixtures import box, make_fragment


SCHEMA = {"video_sources", "audio_sources", "video_step", "max_sequence",
          "written", "missing", "rejected", "ambiguous"}


def synthetic_movie(audio_payload=b"abcdefgh", damage_marker=False):
    header = box(b"ftyp", b"isom" + b"\0" * 12) + box(b"moov", b"\0" * 64)
    fragment, moof, _, _ = make_fragment(
        sequence=0, video_time=0, audio_time=0, audio_payload=audio_payload)
    if damage_marker:
        fragment = bytearray(fragment)
        fragment[len(moof) + 8 + 16] = ord("x")
    return header + fragment


class RebuildCommandTests(unittest.TestCase):
    def run_rebuild(self, video, output, donors=()):
        argv = ["rvc-rebuild", "--video", str(video), "--output", str(output),
                "--max-sequence", "0", "--max-inserted-blocks", "0"]
        for donor in donors:
            argv.extend(("--audio", str(donor)))
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                rebuild.main()
                status = 0
            except SystemExit as exc:
                status = exc.code
        return status, stdout.getvalue(), stderr.getvalue()

    def test_all_rejected_keeps_diagnostic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "synthetic-video.mov"
            video.write_bytes(synthetic_movie(damage_marker=True))
            output = root / "rebuilt.mov"
            manifest = output.with_suffix(".manifest.json")
            status, _, message = self.run_rebuild(video, output)
            self.assertNotEqual(status, 0)
            self.assertFalse(output.exists())
            self.assertTrue(manifest.exists())
            self.assertIn("no fragments passed structural repair", str(status))
            self.assertIn(str(manifest.resolve()), str(status))
            self.assertEqual(message, "")
            report = json.loads(manifest.read_text())
            self.assertEqual(set(report), SCHEMA)
            self.assertEqual(report["written"], [])
            self.assertEqual(report["missing"], [0])
            self.assertIn("marker missing", report["rejected"][0]["reason"])

    def test_all_ambiguous_keeps_diagnostic_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "synthetic-video.mov"
            donor_a = root / "synthetic-a.mov"
            donor_b = root / "synthetic-b.mov"
            video.write_bytes(synthetic_movie())
            donor_a.write_bytes(synthetic_movie(audio_payload=b"abcdefgh"))
            donor_b.write_bytes(synthetic_movie(audio_payload=b"12345678"))
            output = root / "rebuilt.mov"
            status, _, _ = self.run_rebuild(video, output, (donor_a, donor_b))
            manifest = output.with_suffix(".manifest.json")
            self.assertNotEqual(status, 0)
            self.assertFalse(output.exists())
            report = json.loads(manifest.read_text())
            self.assertEqual(set(report), SCHEMA)
            self.assertEqual(report["missing"], [0])
            self.assertEqual(report["written"], [])
            self.assertEqual({entry["path"] for entry in report["ambiguous"][0]["audio_donors"]},
                             {str(donor_a.resolve()), str(donor_b.resolve())})

    def test_success_uses_same_manifest_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "synthetic-video.mov"
            video.write_bytes(synthetic_movie())
            output = root / "rebuilt.mov"
            status, _, _ = self.run_rebuild(video, output)
            manifest = output.with_suffix(".manifest.json")
            self.assertEqual(status, 0)
            self.assertTrue(output.exists())
            self.assertTrue(manifest.exists())
            report = json.loads(manifest.read_text())
            self.assertEqual(set(report), SCHEMA)
            self.assertEqual(len(report["written"]), 1)
            self.assertEqual(report["missing"], [])

    def test_existing_diagnostic_manifest_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "synthetic-video.mov"
            video.write_bytes(synthetic_movie(damage_marker=True))
            output = root / "rebuilt.mov"
            manifest = output.with_suffix(".manifest.json")
            manifest.write_bytes(b"keep")
            status, _, message = self.run_rebuild(video, output)
            self.assertEqual(status, 2)
            self.assertIn("output must be a new file", message)
            self.assertFalse(output.exists())
            self.assertEqual(manifest.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
