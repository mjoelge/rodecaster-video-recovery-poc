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

    def test_raw_destination_failures_are_argparse_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.img"
            source.write_bytes(b"x" * 4096)
            for module, argv in (
                    (capture, ["rvc-capture", str(source), str(root / "capture.img"),
                               "--start-byte", "0", "--length-byte", "512",
                               "--expected-size", "4096", "--anchor-offset", "0",
                               "--anchor-sha256", "0" * 64]),
                    (scan, ["rvc-scan", str(source), "--out-dir", str(root / "scan")])):
                for reason in ("destination is on the raw source disk",
                               "cannot verify separate physical disks for raw source"):
                    with self.subTest(command=module.__name__, reason=reason):
                        stderr = io.StringIO()
                        with mock.patch.object(sys, "argv", argv), \
                             mock.patch.object(module, "ensure_separate_destination",
                                               side_effect=ValueError(reason)) as check, \
                             contextlib.redirect_stderr(stderr):
                            with self.assertRaises(SystemExit) as failure:
                                module.main()
                        self.assertEqual(failure.exception.code, 2)
                        self.assertIn(reason, stderr.getvalue())
                        self.assertNotIn("Traceback", stderr.getvalue())
                        check.assert_called_once()
                        self.assertFalse((root / "capture.img").exists())
                        self.assertFalse((root / "scan").exists())

    def test_unexpected_destination_io_error_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.img"
            source.write_bytes(b"x" * 4096)
            for module, argv in (
                    (capture, ["rvc-capture", str(source), str(root / "capture.img"),
                               "--start-byte", "0", "--length-byte", "512",
                               "--expected-size", "4096", "--anchor-offset", "0",
                               "--anchor-sha256", "0" * 64]),
                    (scan, ["rvc-scan", str(source), "--out-dir", str(root / "scan")])):
                with self.subTest(command=module.__name__), \
                     mock.patch.object(sys, "argv", argv), \
                     mock.patch.object(module, "ensure_separate_destination",
                                       side_effect=OSError("synthetic I/O failure")):
                    with self.assertRaisesRegex(OSError, "synthetic I/O failure"):
                        module.main()


if __name__ == "__main__":
    unittest.main()
