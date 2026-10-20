"""Read-only source sizing and destination-disk checks."""

import json
import os
import plistlib
import stat
import subprocess
import sys
from collections import defaultdict
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


def _existing_directory(destination):
    directory = Path(destination)
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    if not directory.is_dir():
        raise ValueError("destination parent is not a directory")
    return directory.resolve()


def _linux_lsblk():
    result = subprocess.run(
        ["lsblk", "--json", "--bytes", "--output", "NAME,MAJ:MIN,TYPE,PKNAME"],
        check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _linux_physical_disks(listing, device_number):
    """Resolve a disk or partition to its disk using an lsblk device graph.

    Mapped, RAID, loop, and incomplete topologies are rejected. A single
    reported parent for a mapped device is not evidence of all backing disks.
    """
    nodes = {}
    parents = defaultdict(set)
    by_name = {}
    pending = []

    def visit(node, parent=None):
        key = node.get("maj:min")
        name = node.get("name")
        kind = node.get("type")
        if not key or not name or not kind:
            raise ValueError("incomplete lsblk device entry")
        if key in nodes and nodes[key] != kind:
            raise ValueError("conflicting lsblk device entries")
        nodes[key] = kind
        by_name[name] = key
        if parent:
            parents[key].add(parent)
        if node.get("pkname"):
            pending.append((key, node["pkname"]))
        for child in node.get("children", []):
            visit(child, key)

    for root in listing.get("blockdevices", []):
        visit(root)
    for key, name in pending:
        if name not in by_name:
            raise ValueError("lsblk parent device is missing")
        parents[key].add(by_name[name])

    key = f"{os.major(device_number)}:{os.minor(device_number)}"
    if key not in nodes:
        raise ValueError("device is absent from lsblk inventory")
    kind = nodes[key]
    if kind == "disk":
        return {key}
    if kind == "part" and len(parents[key]) == 1:
        parent = next(iter(parents[key]))
        if nodes.get(parent) == "disk":
            return {parent}
    raise ValueError(f"unsupported or unresolved block topology: {kind}")


def _mac_physical_disks(info):
    """Resolve a mounted volume to its physical whole disk(s)."""
    stores = info.get("APFSPhysicalStores")
    if stores is not None:
        if not stores:
            raise ValueError("APFS physical stores are missing")
        disks = set()
        for store in stores:
            if isinstance(store, dict):
                store = store.get("APFSPhysicalStore")
            if not isinstance(store, str):
                raise ValueError("invalid APFS physical store")
            physical = diskutil_info(Path("/dev") / store)
            parent = physical.get("ParentWholeDisk") or (
                physical.get("DeviceIdentifier") if physical.get("WholeDisk") else None)
            if not parent:
                raise ValueError("APFS physical disk is unknown")
            disks.add(parent)
        return disks
    if info.get("FilesystemType", "").lower() == "apfs":
        raise ValueError("APFS physical stores are unavailable")
    parent = info.get("ParentWholeDisk") or (
        info.get("DeviceIdentifier") if info.get("WholeDisk") else None)
    if not parent:
        raise ValueError("destination physical disk is unknown")
    return {parent}


def ensure_separate_destination(source, destination):
    """Reject raw-device output when a separate physical disk is unproven."""
    source = Path(source)
    mode = source.stat().st_mode
    if stat.S_ISREG(mode):
        # A regular image file is already a separate copy of its original disk.
        return
    if sys.platform == "darwin" and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
        try:
            source_info = diskutil_info(source)
            if (source_info.get("Internal") is not False or source_info.get("APFSPhysicalStores") or
                    not source_info.get("WholeDisk", source_info.get("Whole", False))):
                raise ValueError("raw source must be an external whole disk")
            mount = _existing_directory(destination)
            while not os.path.ismount(mount) and mount != mount.parent:
                mount = mount.parent
            dest_info = diskutil_info(mount)
            source_disk = source_info.get("ParentWholeDisk") or source_info.get("DeviceIdentifier")
            dest_disks = _mac_physical_disks(dest_info)
            if not source_disk:
                raise ValueError("physical disk identifier unavailable")
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
            raise ValueError("cannot verify separate physical disks for raw source; "
                             "create an image through a separately verified workflow") from exc
        if source_disk in dest_disks:
            raise ValueError("destination is on the raw source disk")
        return
    if sys.platform.startswith("linux") and stat.S_ISBLK(mode):
        try:
            directory = _existing_directory(destination)
            listing = _linux_lsblk()
            source_disks = _linux_physical_disks(listing, source.stat().st_rdev)
            dest_disks = _linux_physical_disks(listing, directory.stat().st_dev)
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
            raise ValueError("cannot verify separate physical disks for raw source; "
                             "create an image through a separately verified workflow") from exc
        if source_disks & dest_disks:
            raise ValueError("destination is on the raw source disk")
        return
    raise ValueError("raw or special source type is unsupported; use a regular disk image")
