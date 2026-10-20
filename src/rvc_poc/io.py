"""Read-only source sizing and destination-disk checks."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path


def diskutil_info(path):
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(path)],
                            check=True, capture_output=True)
    return plistlib.loads(result.stdout)


def source_size(source, descriptor):
    size = os.fstat(descriptor).st_size
    if size:
        return size
    if sys.platform == "darwin":
        return int(diskutil_info(source)["TotalSize"])
    if sys.platform.startswith("linux"):
        result = subprocess.run(["blockdev", "--getsize64", str(source)],
                                check=True, capture_output=True, text=True)
        return int(result.stdout.strip())
    raise ValueError("raw device capacity unavailable; use a disk image")


def ensure_separate_destination(source, destination):
    """Prevent output on the same physical macOS disk as a raw source."""
    if sys.platform != "darwin" or not str(source).startswith("/dev/"):
        return
    source_info = diskutil_info(source)
    if source_info.get("Internal", False) or not source_info.get(
            "WholeDisk", source_info.get("Whole", False)):
        raise ValueError("raw source must be an external whole disk")
    mount = Path(destination)
    while not os.path.ismount(mount) and mount != mount.parent:
        mount = mount.parent
    dest_info = diskutil_info(mount)
    source_disk = source_info.get("ParentWholeDisk") or source_info.get("DeviceIdentifier")
    dest_disk = dest_info.get("ParentWholeDisk") or dest_info.get("DeviceIdentifier")
    if source_disk == dest_disk:
        raise ValueError("destination is on the raw source disk")
