"""Read-only raw-image scan for movie headers and long fragment timelines."""

import argparse
import csv
import os
import struct
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .io import ensure_separate_destination, source_size

BLOCK = 16 * 1024 * 1024
OVERLAP = 4096
REGION = 128 * 1024 * 1024
EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


def movie_headers(data, base, lower, upper):
    pos = 0
    while True:
        hit = data.find(b"mvhd", pos)
        if hit < 0:
            return
        pos = hit + 4
        start = hit - 4
        if start < 0 or start + 44 > len(data):
            continue
        size = struct.unpack_from(">I", data, start)[0]
        if not 32 <= size <= 4096 or start + size > len(data):
            continue
        version = data[hit + 4]
        if version == 0:
            created, _, scale, duration = struct.unpack_from(">IIII", data, hit + 8)
        elif version == 1 and size >= 44:
            created, _, scale, duration = struct.unpack_from(">QQIQ", data, hit + 8)
        else:
            continue
        if not 1 <= scale <= 10_000_000 or duration / scale > 24 * 3600:
            continue
        try:
            creation = EPOCH + timedelta(seconds=created)
        except OverflowError:
            continue
        if not 1970 <= creation.year <= 2100:
            continue
        absolute = base + start
        if lower <= absolute < upper:
            moov = hit - 12
            moov_offset = base + moov if moov >= 0 and data[moov + 4:moov + 8] == b"moov" else ""
            yield absolute, moov_offset, creation.isoformat(), round(duration / scale, 3)


def long_fragments(data, base, lower, upper, timescale, min_minutes, max_minutes):
    pos = 0
    while True:
        hit = data.find(b"moof", pos)
        if hit < 0:
            return
        pos = hit + 4
        start = hit - 4
        if start < 0 or start + 64 > len(data):
            continue
        size = struct.unpack_from(">I", data, start)[0]
        if not 80 <= size <= 4096 or start + size > len(data):
            continue
        if data[start + 12:start + 16] != b"mfhd":
            continue
        sequence = struct.unpack_from(">I", data, start + 20)[0]
        if not 1 <= sequence <= 1000000:
            continue
        tfdt = data.find(b"tfdt", start + 16, min(start + size, start + 256))
        if tfdt < 0 or tfdt + 16 > start + size or data[tfdt + 4] != 1:
            continue
        decode_time = struct.unpack_from(">Q", data, tfdt + 8)[0]
        minutes = decode_time / timescale / 60
        absolute = base + start
        if lower <= absolute < upper and min_minutes <= minutes <= max_minutes:
            yield absolute, sequence, minutes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="disk image or raw device; opened read only")
    ap.add_argument("--out-dir", required=True, type=Path, help="new local results directory")
    ap.add_argument("--start-byte", type=int, default=0)
    ap.add_argument("--length-byte", type=int, help="default: scan to end of input")
    ap.add_argument("--fragment-timescale", type=int, default=15360,
                    help="video timescale; derive from an intact reference file")
    ap.add_argument("--min-minutes", type=float, default=20)
    ap.add_argument("--max-minutes", type=float, default=120)
    args = ap.parse_args()
    if args.fragment_timescale <= 0 or not 0 <= args.min_minutes < args.max_minutes:
        ap.error("invalid time parameters")
    source = args.input.expanduser().resolve(strict=True)
    out = args.out_dir.expanduser().resolve()
    if out.exists() or out == source or source in out.parents:
        ap.error("out-dir must be new and outside the input")
    try:
        ensure_separate_destination(source, out.parent)
    except ValueError as exc:
        ap.error(str(exc))
    with source.open("rb") as f:
        size = source_size(source, f.fileno())
        start = args.start_byte
        end = size if args.length_byte is None else start + args.length_byte
        if not 0 <= start < end <= size or start % 512 or end % 512:
            ap.error("scan range must fit input and be sector aligned")
        headers = []
        regions = defaultdict(lambda: [0, float("inf"), 0.0, 1000001, 0])
        tail = b""
        cursor = start
        began = time.monotonic()
        while cursor < end:
            count = min(BLOCK, end - cursor)
            data = os.pread(f.fileno(), count, cursor)
            if len(data) != count:
                raise OSError(f"short read at byte {cursor}")
            window = tail + data
            base = cursor - len(tail)
            headers.extend(movie_headers(window, base, cursor, cursor + count))
            for offset, sequence, minutes in long_fragments(
                    window, base, cursor, cursor + count, args.fragment_timescale,
                    args.min_minutes, args.max_minutes):
                r = regions[offset // REGION]
                r[0] += 1
                r[1] = min(r[1], minutes)
                r[2] = max(r[2], minutes)
                r[3] = min(r[3], sequence)
                r[4] = max(r[4], sequence)
            tail = window[-OVERLAP:]
            cursor += count
            if cursor == end or (cursor - start) // (25 * 10**9) != (cursor - start - count) // (25 * 10**9):
                rate = (cursor - start) / max(1, time.monotonic() - began) / 1e6
                print(f"{(cursor-start)/1e9:.1f}/{(end-start)/1e9:.1f} GB; "
                      f"{len(headers)} headers; {len(regions)} regions; {rate:.1f} MB/s", flush=True)
    out.mkdir(parents=True)
    with (out / "movie_headers.csv").open("x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("disk_offset", "moov_offset", "creation_utc", "duration_seconds"))
        writer.writerows(headers)
    with (out / "fragment_regions.csv").open("x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("region_start", "fragment_count", "min_minutes", "max_minutes",
                         "min_sequence", "max_sequence"))
        for key, (count, low, high, min_seq, max_seq) in sorted(regions.items()):
            writer.writerow((key * REGION, count, round(low, 2), round(high, 2), min_seq, max_seq))
    print(f"Saved metadata summaries to {out}")


if __name__ == "__main__":
    main()
