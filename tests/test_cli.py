import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rvc_poc import capture, catalog, rebuild, scan


class CommandLineTests(unittest.TestCase):
    def test_all_entry_points_import(self):
        for module in (capture, catalog, rebuild, scan):
            self.assertTrue(callable(module.main))

    def test_existing_rebuild_output_or_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.mov"
            source.write_bytes(b"not a media file")
            output = root / "rebuilt.mov"
            for existing in (output, output.with_suffix(".manifest.json")):
                existing.write_bytes(b"keep")
                with mock.patch.object(sys, "argv", ["rvc-rebuild", "--video", str(source),
                                                         "--output", str(output)]):
                    with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                        rebuild.main()
                self.assertEqual(existing.read_bytes(), b"keep")
                existing.unlink()

    def test_other_commands_refuse_existing_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.img"
            source.write_bytes(b"x" * 4096)
            destination = root / "already-exists"
            destination.write_bytes(b"keep")
            with mock.patch.object(sys, "argv", ["rvc-capture", str(source), str(destination),
                                                  "--start-byte", "0", "--length-byte", "512",
                                                  "--expected-size", "4096", "--anchor-offset", "0",
                                                  "--anchor-sha256", "0" * 64]):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    capture.main()
            with mock.patch.object(sys, "argv", ["rvc-catalog", str(root),
                                                  "--out", str(destination)]):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    catalog.main()
            with mock.patch.object(sys, "argv", ["rvc-scan", str(source),
                                                  "--out-dir", str(root)]):
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    scan.main()
            self.assertEqual(destination.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
