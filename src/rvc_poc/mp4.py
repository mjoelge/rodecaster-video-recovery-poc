"""Small ISO BMFF parser for the fragmented two-track layout used here.

This is deliberately conservative: an unknown box layout is rejected rather
than guessed. Track 1 is expected to hold HEVC video and track 2 AAC audio.
"""

import struct


def boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        yield kind, pos, size
        pos += size


def fragment(data, off):
    """Return (sequence, moof_size, mdat_size, tracks) or None.

    Each track value is (decode_time, samples, data_offset, base, sample_bytes).
    The mdat may be truncated: its declared size is still useful for cataloging.
    """
    if off < 0 or off + 24 > len(data):
        return None
    size, kind = struct.unpack_from(">I4s", data, off)
    if kind != b"moof" or not 24 <= size <= 65536 or off + size + 8 > len(data):
        return None
    if data[off + 12:off + 16] != b"mfhd":
        return None
    sequence = struct.unpack_from(">I", data, off + 20)[0]
    tracks = {}
    for kind, boxoff, boxsize in boxes(data, off + 8, off + size):
        if kind != b"traf":
            continue
        track = decode_time = samples = sample_bytes = None
        base = data_offset = None
        for child, pos, length in boxes(data, boxoff + 8, boxoff + boxsize):
            if child == b"tfhd" and length >= 16:
                track = struct.unpack_from(">I", data, pos + 12)[0]
                flags = int.from_bytes(data[pos + 9:pos + 12])
                base = struct.unpack_from(">Q", data, pos + 16)[0] if flags & 1 and length >= 24 else off
            elif child == b"tfdt" and length >= 16:
                version = data[pos + 8]
                if version == 1 and length >= 20:
                    decode_time = struct.unpack_from(">Q", data, pos + 12)[0]
                elif version == 0:
                    decode_time = struct.unpack_from(">I", data, pos + 12)[0]
            elif child == b"trun" and length >= 16:
                flags = int.from_bytes(data[pos + 9:pos + 12])
                samples = struct.unpack_from(">I", data, pos + 12)[0]
                data_offset = struct.unpack_from(">i", data, pos + 16)[0] if flags & 1 and length >= 20 else 0
                cursor = pos + 16 + 4 * bool(flags & 1) + 4 * bool(flags & 4)
                width = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
                size_column = 4 * bool(flags & 0x100)
                if flags & 0x200 and width and samples < 100000 and cursor + width * samples <= pos + length:
                    sample_bytes = sum(struct.unpack_from(">I", data, cursor + width * i + size_column)[0]
                                       for i in range(samples))
        if track is not None:
            tracks[track] = (decode_time, samples, data_offset, base, sample_bytes)
    if 1 not in tracks or 2 not in tracks:
        return None
    mdat_off = off + size
    mdat_size, mdat_kind = struct.unpack_from(">I4s", data, mdat_off)
    if mdat_kind != b"mdat":
        return None
    if mdat_size == 1 and mdat_off + 16 <= len(data):
        mdat_size = struct.unpack_from(">Q", data, mdat_off + 8)[0]
    if mdat_size < 8:
        return None
    return sequence, size, mdat_size, tracks


def scan_fragments(data):
    pos = 0
    while True:
        hit = data.find(b"moof", pos)
        if hit < 0:
            return
        pos = hit + 4
        off = hit - 4
        parsed = fragment(data, off)
        if parsed is not None:
            yield off, parsed


def children_by_track(moof):
    result = {}
    for kind, pos, size in boxes(moof, 8, len(moof)):
        if kind != b"traf":
            continue
        children = {k: (p, n) for k, p, n in boxes(moof, pos + 8, pos + size)}
        if b"tfhd" in children:
            tfhd, _ = children[b"tfhd"]
            result[struct.unpack_from(">I", moof, tfhd + 12)[0]] = children
    return result


def video_duration(moof):
    track = children_by_track(moof)[1]
    trun, length = track[b"trun"]
    flags = int.from_bytes(moof[trun + 9:trun + 12])
    count = struct.unpack_from(">I", moof, trun + 12)[0]
    if not flags & 0x100:
        tfhd, tfhd_size = track[b"tfhd"]
        tfhd_flags = int.from_bytes(moof[tfhd + 9:tfhd + 12])
        cursor = tfhd + 16 + 8 * bool(tfhd_flags & 1) + 4 * bool(tfhd_flags & 2)
        if not tfhd_flags & 8 or cursor + 4 > tfhd + tfhd_size:
            raise ValueError("video sample duration unavailable")
        return count * struct.unpack_from(">I", moof, cursor)[0]
    cursor = trun + 16 + 4 * bool(flags & 1) + 4 * bool(flags & 4)
    width = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
    if cursor + count * width > trun + length:
        raise ValueError("incomplete video sample table")
    return sum(struct.unpack_from(">I", moof, cursor + i * width)[0] for i in range(count))


def video_sample_sizes(moof):
    trun, length = children_by_track(moof)[1][b"trun"]
    flags = int.from_bytes(moof[trun + 9:trun + 12])
    count = struct.unpack_from(">I", moof, trun + 12)[0]
    if not flags & 0x200 or count < 2:
        raise ValueError("video trun has no per-sample sizes")
    cursor = trun + 16 + 4 * bool(flags & 1) + 4 * bool(flags & 4)
    width = 4 * sum(bool(flags & bit) for bit in (0x100, 0x200, 0x400, 0x800))
    size_column = 4 * bool(flags & 0x100)
    if cursor + count * width > trun + length:
        raise ValueError("incomplete video sample table")
    return [struct.unpack_from(">I", moof, cursor + i * width + size_column)[0]
            for i in range(count)]


def init_header(data, first_moof):
    """Expose a carved file's embedded initialization moov without changing length."""
    head = bytearray(data[:first_moof])
    if len(head) < 64 or head[4:8] != b"ftyp" or b"moov" not in head:
        raise ValueError("opening ftyp/moov not found")
    ftyp_size = struct.unpack_from(">I", head, 0)[0]
    if not 8 <= ftyp_size <= len(head) - 8:
        raise ValueError("invalid ftyp size")
    if head[ftyp_size + 4:ftyp_size + 8] == b"mdat":
        if head[ftyp_size + 20:ftyp_size + 24] == b"moov":
            head[ftyp_size:ftyp_size + 16] = struct.pack(">I4sQ", 16, b"free", 0)
        elif struct.unpack_from(">I", head, ftyp_size)[0] == 1:
            head[ftyp_size:ftyp_size + 16] = struct.pack(">I4sQ", 1, b"free", 16)
        else:
            head[ftyp_size:ftyp_size + 8] = struct.pack(">I4s", 8, b"free")
    return bytes(head)


def patch_base(moof, output_offset):
    for track in children_by_track(moof).values():
        pos, size = track[b"tfhd"]
        if size < 24 or not int.from_bytes(moof[pos + 9:pos + 12]) & 1:
            raise ValueError("tfhd lacks explicit base-data-offset")
        struct.pack_into(">Q", moof, pos + 16, output_offset)


def hybrid_fragment(video, audio, output_offset):
    """Combine video and audio payloads from matching timeline fragments."""
    moof = bytearray(video["moof"])
    vt = children_by_track(moof)
    at = children_by_track(audio["moof"])
    if set(vt) != {1, 2} or set(at) != {1, 2}:
        raise ValueError("expected video track 1 and audio track 2")
    for kind in (b"trun", b"tfdt"):
        dest, length = vt[2][kind]
        source, source_length = at[2][kind]
        if length != source_length:
            raise ValueError("audio table shape differs between donors")
        moof[dest:dest + length] = audio["moof"][source:source + length]
    struct.pack_into(">I", moof, 20, video["sequence"])
    video_tfdt, _ = vt[1][b"tfdt"]
    if moof[video_tfdt + 8] == 1:
        struct.pack_into(">Q", moof, video_tfdt + 12, video["video_time"])
    else:
        struct.pack_into(">I", moof, video_tfdt + 12, video["video_time"])
    audio_trun, _ = vt[2][b"trun"]
    if not int.from_bytes(moof[audio_trun + 9:audio_trun + 12]) & 1:
        raise ValueError("audio trun lacks a data offset")
    struct.pack_into(">i", moof, audio_trun + 16, len(moof) + 8 + len(video["video"]))
    patch_base(moof, output_offset)
    payload = video["video"] + audio["audio"]
    return bytes(moof) + struct.pack(">I4s", len(payload) + 8, b"mdat") + payload
