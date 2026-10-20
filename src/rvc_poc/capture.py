"""Copy an exact byte range from a disk image or raw device without writing to it."""

import argparse
import hashlib
import os
from pathlib import Path

from .io import ensure_separate_destination, source_size

BLOCK = 8 * 1024 * 1024


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="source image or raw device")
    ap.add_argument("output", type=Path, help="new capture file on another disk")
    ap.add_argument("--start-byte", required=True, type=int)
    ap.add_argument("--length-byte", required=True, type=int)
    ap.add_argument("--expected-size", required=True, type=int,
                    help="known source capacity in bytes")
    ap.add_argument("--anchor-offset", required=True, type=int,
                    help="offset of a known 4096-byte anchor on the source")
    ap.add_argument("--anchor-sha256", required=True,
                    help="SHA-256 of the anchor, computed independently")
    args = ap.parse_args()
    source = args.input.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    start, length = args.start_byte, args.length_byte
    if output.exists() or source == output:
        ap.error("output must be a new path distinct from the source")
    try:
        ensure_separate_destination(source, output.parent)
    except ValueError as exc:
        ap.error(str(exc))
    if (start < 0 or length <= 0 or args.anchor_offset < 0 or
            any(n % 512 for n in (start, length, args.anchor_offset))):
        ap.error("offsets and length must be nonnegative and sector aligned")
    with source.open("rb") as f:
        size = source_size(source, f.fileno())
        if size != args.expected_size or start + length > size or args.anchor_offset + 4096 > size:
            ap.error("source capacity or requested range differs from expected values")
        anchor = os.pread(f.fileno(), 4096, args.anchor_offset)
        if hashlib.sha256(anchor).hexdigest().lower() != args.anchor_sha256.lower():
            ap.error("source anchor hash mismatch")
        output.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with output.open("xb") as dest:
            while written < length:
                count = min(BLOCK, length - written)
                data = os.pread(f.fileno(), count, start + written)
                if len(data) != count:
                    raise OSError(f"short source read at byte {start + written}")
                dest.write(data)
                written += count
                if written == length or written // (128 * 1024 * 1024) != (written - count) // (128 * 1024 * 1024):
                    print(f"{written}/{length} bytes copied", flush=True)
    print(f"Saved capture to {output}")


if __name__ == "__main__":
    main()
