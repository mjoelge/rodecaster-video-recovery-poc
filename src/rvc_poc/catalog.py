"""Catalog fragmented MOV/MP4 files without copying their media payloads."""

import argparse
import mmap
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from .mp4 import scan_fragments


def dfxml_runs(path):
    """Map unique PhotoRec filenames to original disk byte runs, if available."""
    found = {}
    for _, node in ET.iterparse(path, events=("end",)):
        if not node.tag.endswith("fileobject"):
            continue
        name = next((child.text for child in node if child.tag.endswith("filename")), None)
        if name:
            runs = []
            for child in node.iter():
                if child.tag.endswith("byte_run") and "img_offset" in child.attrib:
                    runs.append(tuple(int(child.attrib[key]) for key in ("offset", "img_offset", "len")))
            found.setdefault(Path(name).name, []).append(runs)
        node.clear()
    return {name: values[0] for name, values in found.items() if len(values) == 1}


def disk_offset(runs, file_offset):
    for carved_offset, image_offset, length in runs:
        if carved_offset <= file_offset < carved_offset + length:
            return image_offset + file_offset - carved_offset
    return None


def catalog_one(con, path, relative_path, runs):
    size = path.stat().st_size
    count = 0
    if size >= 32:
        with path.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for off, parsed in scan_fragments(data):
                sequence, moof_size, mdat_size, tracks = parsed
                video, audio = tracks[1], tracks[2]
                con.execute(
                    "INSERT INTO fragment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (relative_path, off, disk_offset(runs, off), sequence, moof_size, mdat_size,
                     video[0], audio[0], video[1], audio[1], video[2], audio[2],
                     video[4], audio[4], video[3], audio[3]),
                )
                count += 1
    con.execute("INSERT INTO source VALUES (?,?,?)", (relative_path, size, count))
    con.commit()
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_dir", type=Path, help="directory containing recovered files")
    ap.add_argument("--out", required=True, type=Path, help="local SQLite catalog")
    ap.add_argument("--dfxml", type=Path, help="optional PhotoRec DFXML report")
    args = ap.parse_args()
    root = args.input_dir.expanduser().resolve(strict=True)
    if not root.is_dir():
        ap.error("input_dir must be a directory")
    output = args.out.expanduser().resolve()
    if output.exists():
        ap.error("output already exists; choose a fresh path")
    runs = dfxml_runs(args.dfxml) if args.dfxml else {}
    paths = sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("._")
                   and p.suffix.lower() in {".mov", ".mp4", ".apple"} and p.resolve() != output)
    if not paths:
        ap.error("no MOV/MP4 candidates found")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.executescript("""
            CREATE TABLE source(path TEXT PRIMARY KEY, bytes INTEGER, fragment_count INTEGER);
            CREATE TABLE fragment(
                path TEXT, file_offset INTEGER, disk_offset INTEGER, sequence INTEGER,
                moof_size INTEGER, mdat_size INTEGER, video_time INTEGER, audio_time INTEGER,
                video_samples INTEGER, audio_samples INTEGER, video_data INTEGER, audio_data INTEGER,
                video_bytes INTEGER, audio_bytes INTEGER, video_base INTEGER, audio_base INTEGER,
                PRIMARY KEY(path, file_offset));
            CREATE INDEX fragment_sequence ON fragment(sequence, video_time, audio_time);
            CREATE INDEX fragment_disk ON fragment(disk_offset);
        """)
        for i, path in enumerate(paths, 1):
            name = str(path.relative_to(root))
            count = catalog_one(con, path, name, runs.get(path.name, []))
            print(f"{i}/{len(paths)} {name}: {count} fragments", flush=True)
    print(f"Catalog saved to {output}")


if __name__ == "__main__":
    main()
