import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from rvc_poc.io import (_linux_physical_disks, ensure_separate_destination,
                        source_size)


DISKS = {"blockdevices": [
    {"name": "sda", "maj:min": "8:0", "type": "disk", "children": [
        {"name": "sda1", "maj:min": "8:1", "type": "part", "pkname": "sda"},
        {"name": "crypt-a", "maj:min": "253:0", "type": "crypt", "pkname": "sda"}]},
    {"name": "sdb", "maj:min": "8:16", "type": "disk", "children": [
        {"name": "sdb1", "maj:min": "8:17", "type": "part", "pkname": "sdb"}]},
]}


class SourceSizeTests(unittest.TestCase):
    def test_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.img"
            source.write_bytes(b"x" * 512)
            with source.open("rb") as stream:
                self.assertEqual(source_size(source, stream.fileno()), 512)

    def test_mocked_linux_raw_size(self):
        with mock.patch("rvc_poc.io.os.fstat", return_value=SimpleNamespace(st_size=0)), \
             mock.patch("rvc_poc.io.sys.platform", "linux"), \
             mock.patch("rvc_poc.io.subprocess.run",
                        return_value=SimpleNamespace(stdout="8192\n")):
            self.assertEqual(source_size(Path("/dev/synthetic"), 1), 8192)

    def test_mocked_macos_raw_size(self):
        with mock.patch("rvc_poc.io.os.fstat", return_value=SimpleNamespace(st_size=0)), \
             mock.patch("rvc_poc.io.sys.platform", "darwin"), \
             mock.patch("rvc_poc.io.diskutil_info", return_value={"TotalSize": 16384}):
            self.assertEqual(source_size(Path("/dev/synthetic-raw"), 1), 16384)


class DestinationTests(unittest.TestCase):
    def test_linux_parent_disks_and_unsupported_mapper(self):
        self.assertEqual(_linux_physical_disks(DISKS, os.makedev(8, 17)), {"8:16"})
        with self.assertRaisesRegex(ValueError, "unsupported"):
            _linux_physical_disks(DISKS, os.makedev(253, 0))

    def linux_check(self, destination_number, source="/dev/synthetic-alias"):
        source_stat = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 16))
        dest_stat = SimpleNamespace(st_mode=stat.S_IFDIR, st_dev=destination_number)

        def fake_stat(path, *args, **kwargs):
            return source_stat if str(path) == source else dest_stat

        return (mock.patch("rvc_poc.io.sys.platform", "linux"),
                mock.patch.object(Path, "stat", autospec=True, side_effect=fake_stat),
                mock.patch("rvc_poc.io._existing_directory", return_value=Path("/synthetic/destination")),
                mock.patch("rvc_poc.io._linux_lsblk", return_value=DISKS))

    def test_linux_same_disk_rejected_and_other_disk_allowed(self):
        patches = self.linux_check(os.makedev(8, 17))
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(ValueError, "source disk"):
                ensure_separate_destination(Path("/dev/synthetic-alias"), Path("/synthetic/destination"))
        patches = self.linux_check(os.makedev(8, 1))
        with patches[0], patches[1], patches[2], patches[3]:
            ensure_separate_destination(Path("/dev/synthetic-alias"), Path("/synthetic/destination"))

    def test_linux_mapper_and_unavailable_topology_fail_closed(self):
        patches = self.linux_check(os.makedev(253, 0))
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                ensure_separate_destination(Path("/dev/synthetic-alias"), Path("/synthetic/destination"))
        patches = self.linux_check(os.makedev(8, 1))
        with patches[0], patches[1], patches[2], patches[3] as inventory:
            inventory.side_effect = FileNotFoundError("lsblk absent")
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                ensure_separate_destination(Path("/dev/synthetic-alias"), Path("/synthetic/destination"))

    def test_macos_same_disk_and_unknown_identity_rejected(self):
        source = Path("/dev/synthetic-raw")
        destination = Path("/synthetic/destination")
        source_stat = SimpleNamespace(st_mode=stat.S_IFCHR)
        with mock.patch("rvc_poc.io.sys.platform", "darwin"), \
             mock.patch.object(Path, "stat", return_value=source_stat), \
             mock.patch("rvc_poc.io._existing_directory", return_value=destination), \
             mock.patch("rvc_poc.io.os.path.ismount", return_value=True), \
             mock.patch("rvc_poc.io.diskutil_info", side_effect=[
                 {"Internal": False, "WholeDisk": True, "DeviceIdentifier": "diskSource"},
                 {"ParentWholeDisk": "diskSource"}]):
            with self.assertRaisesRegex(ValueError, "source disk"):
                ensure_separate_destination(source, destination)
        with mock.patch("rvc_poc.io.sys.platform", "darwin"), \
             mock.patch.object(Path, "stat", return_value=source_stat), \
             mock.patch("rvc_poc.io._existing_directory", return_value=destination), \
             mock.patch("rvc_poc.io.os.path.ismount", return_value=True), \
             mock.patch("rvc_poc.io.diskutil_info", side_effect=[
                 {"Internal": False, "WholeDisk": True, "DeviceIdentifier": "diskSource"}, {}]):
            with self.assertRaisesRegex(ValueError, "cannot verify"):
                ensure_separate_destination(source, destination)

    def test_macos_apfs_store_checks_physical_disk(self):
        source = Path("/dev/synthetic-raw")
        destination = Path("/synthetic/destination")
        source_stat = SimpleNamespace(st_mode=stat.S_IFCHR)
        with mock.patch("rvc_poc.io.sys.platform", "darwin"), \
             mock.patch.object(Path, "stat", return_value=source_stat), \
             mock.patch("rvc_poc.io._existing_directory", return_value=destination), \
             mock.patch("rvc_poc.io.os.path.ismount", return_value=True), \
             mock.patch("rvc_poc.io.diskutil_info", side_effect=[
                 {"Internal": False, "WholeDisk": True, "DeviceIdentifier": "diskSource"},
                 {"FilesystemType": "apfs", "ParentWholeDisk": "diskContainer",
                  "APFSPhysicalStores": [{"APFSPhysicalStore": "diskSourcePart"}]},
                 {"ParentWholeDisk": "diskSource"}]):
            with self.assertRaisesRegex(ValueError, "source disk"):
                ensure_separate_destination(source, destination)

    def test_regular_image_stays_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "synthetic.img"
            image.write_bytes(b"x")
            ensure_separate_destination(image, Path(directory))


if __name__ == "__main__":
    unittest.main()
