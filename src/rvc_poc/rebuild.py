"""Conservative fragmented MOV reconstruction from carved files.

Inputs are read only. A new output and a local manifest are created. This
proof of concept supports a two-track HEVC/AAC layout and optional aligned
allocation-block intrusions; other layouts need an adapter.
"""

import argparse
import json
import mmap
import struct
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path

from .mp4 import (audio_signature, fragment, hybrid_fragment, init_header, scan_fragments,
                  video_duration, video_sample_sizes)


def repair_fragment(data, row, block_size=131072, max_blocks=128):
    """Remove inserted aligned blocks by following HEVC sample boundaries."""
    off = row["offset"]
    parsed = fragment(data, off)
    if parsed is None:
        raise ValueError("invalid moof/mdat")
    sequence, moof_size, mdat_size, tracks = parsed
    video, audio = tracks[1], tracks[2]
    if (sequence != row["sequence"] or video[4] is None or audio[4] is None or
            audio[1] is None or audio[0] is None or
            mdat_size != 8 + video[4] + audio[4]):
        raise ValueError("unsupported payload layout")
    moof = bytes(data[off:off + moof_size])
    sizes = video_sample_sizes(moof)
    if sum(sizes) != video[4]:
        raise ValueError("video sample table disagrees with mdat")
    payload_start = off + moof_size + 8
    nominal = payload_start
    last_shift = 0
    previous_actual = payload_start
    deletions = []
    for i, size in enumerate(sizes):
        if size < 6:
            raise ValueError("HEVC sample too small")
        if i:
            marker = struct.pack(">I", size - 4) + b"\x02\x01"
            shift = next((k for k in range(last_shift, max_blocks + 1)
                          if nominal + k * block_size + 6 <= len(data) and
                          data[nominal + k * block_size:nominal + k * block_size + 6] == marker), None)
            if shift is None:
                raise ValueError(f"sample {i} marker missing")
            if shift > last_shift:
                boundary = (previous_actual // block_size + 1) * block_size
                if boundary <= payload_start or boundary > nominal + shift * block_size:
                    raise ValueError("insertion boundary cannot be located")
                deletions.append((boundary, (shift - last_shift) * block_size))
            last_shift = shift
        previous_actual = nominal + last_shift * block_size
        nominal += size
    physical_end = off + moof_size + mdat_size + last_shift * block_size
    if physical_end > len(data):
        raise ValueError("fragment extends beyond carved file")
    mdat_start = off + moof_size
    cursor = mdat_start
    spans = []
    for boundary, length in deletions:
        if not cursor <= boundary <= physical_end or boundary + length > physical_end:
            raise ValueError("inserted block overlaps fragment boundary")
        spans.append(bytes(data[cursor:boundary]))
        cursor = boundary + length
    spans.append(bytes(data[cursor:physical_end]))
    mdat = b"".join(spans)
    if len(mdat) != mdat_size or mdat[4:8] != b"mdat":
        raise ValueError("reconstructed mdat length is invalid")
    video_payload = mdat[8:8 + video[4]]
    cursor = sizes[0]
    for i, size in enumerate(sizes[1:], 1):
        marker = struct.pack(">I", size - 4) + b"\x02\x01"
        if video_payload[cursor:cursor + 6] != marker:
            raise ValueError(f"reconstructed sample {i} is misaligned")
        cursor += size
    return {
        "sequence": sequence, "moof": moof,
        "video": video_payload, "audio": mdat[8 + video[4]:],
        "video_time": video[0], "audio_time": audio[0],
        "audio_samples": audio[1], "audio_bytes": audio[4],
        "inserted_blocks_removed": last_shift,
    }


def index(data, path):
    rows = defaultdict(list)
    for off, parsed in scan_fragments(data):
        sequence, _, _, tracks = parsed
        video, audio = tracks[1], tracks[2]
        if None in (video[0], audio[0], video[4], audio[4]):
            continue
        rows[sequence].append({"path": path, "offset": off, "sequence": sequence,
                               "video_time": video[0], "audio_time": audio[0],
                               "video_bytes": video[4], "audio_bytes": audio[4]})
    return rows


def reference(row):
    return {"path": str(row["path"]), "offset": row["offset"]}


def select_audio_donor(video, candidates):
    """Choose one structurally matching audio payload, never by size alone.

    Candidates are (catalog_row, repaired_fragment) pairs. Exact duplicate
    audio bytes collapse; distinct compatible payloads remain ambiguous.
    """
    expected = audio_signature(video)
    unique = {}
    rejected = []
    for row, item in candidates:
        ref = reference(row)
        try:
            if audio_signature(item) != expected:
                rejected.append({**ref, "reason": "audio timing or table differs"})
                continue
        except (ValueError, KeyError, IndexError, struct.error) as exc:
            rejected.append({**ref, "reason": str(exc)})
            continue
        unique.setdefault(item["audio"], (row, item))
    if len(unique) > 1:
        return None, rejected, [reference(row) for row, _ in unique.values()]
    return (next(iter(unique.values())) if unique else None), rejected, []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", action="append", required=True, type=Path,
                    help="carved MOV/MP4 containing the target video (repeatable)")
    ap.add_argument("--audio", action="append", type=Path, default=[],
                    help="optional matching-session audio donor (repeatable)")
    ap.add_argument("--output", required=True, type=Path, help="new fragmented MOV path")
    ap.add_argument("--max-sequence", type=int, help="last fragment sequence to attempt")
    ap.add_argument("--min-video-bytes", type=int, default=0)
    ap.add_argument("--max-video-bytes", type=int)
    ap.add_argument("--block-size", type=int, default=131072)
    ap.add_argument("--max-inserted-blocks", type=int, default=128)
    args = ap.parse_args()
    if args.block_size <= 0 or args.max_inserted_blocks < 0:
        ap.error("block parameters must be nonnegative, with positive block size")
    video_paths = [p.expanduser().resolve(strict=True) for p in args.video]
    audio_paths = [p.expanduser().resolve(strict=True) for p in args.audio]
    paths = list(dict.fromkeys(video_paths + audio_paths))
    output = args.output.expanduser().resolve()
    manifest = output.with_suffix(".manifest.json")
    if output in paths or output.exists() or manifest.exists():
        ap.error("output must be a new file distinct from all inputs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        data = {}
        indexes = {}
        for path in paths:
            source = stack.enter_context(path.open("rb"))
            if path.stat().st_size < 32:
                ap.error(f"input too small: {path}")
            data[path] = stack.enter_context(mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ))
            indexes[path] = index(data[path], path)
        opener = next((row for row in indexes[video_paths[0]].get(0, [])
                       if row["video_time"] == 0), None)
        if opener is None:
            ap.error("first video file needs a sequence-zero opening fragment")
        first_moof = opener["offset"]
        head = init_header(data[video_paths[0]], first_moof)
        moof_size = fragment(data[video_paths[0]], first_moof)[1]
        step = video_duration(data[video_paths[0]][first_moof:first_moof + moof_size])
        if step <= 0:
            ap.error("invalid video time step")
        candidates = defaultdict(list)
        for path in video_paths:
            for seq, rows in indexes[path].items():
                candidates[seq].extend(r for r in rows if r["video_time"] == seq * step and
                                       args.min_video_bytes <= r["video_bytes"] and
                                       (args.max_video_bytes is None or
                                        r["video_bytes"] <= args.max_video_bytes))
        max_seq = args.max_sequence if args.max_sequence is not None else max(candidates, default=-1)
        if max_seq < 0 or max_seq > 100000:
            ap.error("invalid or unavailable sequence range")
        audio_index = defaultdict(list)
        for path in audio_paths:
            for seq, rows in indexes[path].items():
                audio_index[seq].extend(rows)
        report = {"video_sources": [str(p) for p in video_paths],
                  "audio_sources": [str(p) for p in audio_paths],
                  "video_step": step, "max_sequence": max_seq,
                  "written": [], "missing": [], "rejected": [], "ambiguous": []}
        with output.open("xb") as dest:
            dest.write(head)
            for seq in range(max_seq + 1):
                options = sorted(candidates.get(seq, []),
                                 key=lambda r: (video_paths.index(r["path"]), -r["video_bytes"], r["offset"]))
                if not options:
                    report["missing"].append(seq)
                    continue
                written = False
                for row in options:
                    try:
                        video = repair_fragment(data[row["path"]], row,
                                                args.block_size, args.max_inserted_blocks)
                    except (ValueError, KeyError, IndexError, struct.error) as exc:
                        report["rejected"].append({"sequence": seq, **reference(row),
                                                   "reason": str(exc)})
                        continue
                    if not audio_paths:
                        chosen = (row, video)
                    else:
                        try:
                            audio_signature(video)
                        except (ValueError, KeyError, IndexError, struct.error) as exc:
                            report["rejected"].append({"sequence": seq, **reference(row),
                                                       "reason": str(exc)})
                            break
                        donor_candidates = []
                        for donor in audio_index.get(seq, []):
                            if donor["audio_time"] != row["audio_time"]:
                                report["rejected"].append({"sequence": seq, **reference(donor),
                                                           "reason": "audio decode time differs"})
                                continue
                            try:
                                item = repair_fragment(data[donor["path"]], donor,
                                                       args.block_size, args.max_inserted_blocks)
                                donor_candidates.append((donor, item))
                            except (ValueError, KeyError, IndexError, struct.error) as exc:
                                report["rejected"].append({"sequence": seq, **reference(donor),
                                                           "reason": str(exc)})
                        chosen, rejected, ambiguous = select_audio_donor(video, donor_candidates)
                        report["rejected"].extend({"sequence": seq, **entry} for entry in rejected)
                        if ambiguous:
                            report["ambiguous"].append({"sequence": seq, "video": reference(row),
                                                        "audio_donors": ambiguous})
                            break
                        if chosen is None:
                            report["rejected"].append({"sequence": seq, **reference(row),
                                                       "reason": "no compatible audio donor"})
                            break
                    donor, audio = chosen
                    try:
                        payload = hybrid_fragment(video, audio, dest.tell())
                    except (ValueError, KeyError, IndexError, struct.error) as exc:
                        report["rejected"].append({"sequence": seq, **reference(donor),
                                                   "reason": str(exc)})
                        break
                    dest.write(payload)
                    report["written"].append({
                        "sequence": seq, "video_source": str(row["path"]),
                        "video_offset": row["offset"], "audio_source": str(donor["path"]),
                        "audio_offset": donor["offset"],
                        "inserted_blocks_removed": video["inserted_blocks_removed"]})
                    written = True
                    break
                if not written:
                    report["missing"].append(seq)
                if seq and seq % 100 == 0:
                    print(f"sequence {seq}/{max_seq}; written {len(report['written'])}", flush=True)
        if not report["written"]:
            output.unlink()
            raise SystemExit("no fragments passed structural repair")
        manifest.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {len(report['written'])} fragments to {output}")
    print(f"Missing sequences: {len(report['missing'])}; manifest: {manifest}")


if __name__ == "__main__":
    main()
