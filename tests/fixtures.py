"""In-memory synthetic ISO BMFF fragments; no recorded media is used."""

import struct


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def make_fragment(sequence=1, video_sizes=(16, 16), audio_sizes=(4, 4),
                  audio_payload=None, video_time=None, audio_time=None,
                  video_durations=None):
    if video_time is None:
        video_time = sequence * 1024
    if audio_time is None:
        audio_time = sequence * 2048
    if audio_payload is None:
        audio_payload = b"a" * sum(audio_sizes)
    assert len(audio_payload) == sum(audio_sizes)
    video_samples = []
    for i, size in enumerate(video_sizes):
        if i == 0:
            video_samples.append(b"V" * size)
        else:
            video_samples.append(struct.pack(">I", size - 4) + b"\x02\x01" + b"v" * (size - 6))
    video_payload = b"".join(video_samples)

    def traf(track, decode_time, sizes, duration, durations=None):
        tfhd = box(b"tfhd", struct.pack(">IIQ", 0x1 if durations else 0x9, track, 0) +
                   (b"" if durations else struct.pack(">I", duration)))
        tfdt = box(b"tfdt", struct.pack(">IQ", 0x01000000, decode_time))
        flags = 0x301 if durations else 0x201
        entries = (b"".join(struct.pack(">II", dur, size)
                            for dur, size in zip(durations, sizes)) if durations else
                   b"".join(struct.pack(">I", size) for size in sizes))
        trun = box(b"trun", struct.pack(">IIi", flags, len(sizes), 0) + entries)
        return box(b"traf", tfhd + tfdt + trun)

    moof = box(b"moof", box(b"mfhd", struct.pack(">II", 0, sequence)) +
               traf(1, video_time, video_sizes, 512, video_durations) +
               traf(2, audio_time, audio_sizes, 1024))
    mdat = box(b"mdat", video_payload + audio_payload)
    return moof + mdat, moof, video_payload, audio_payload


def row(offset=0, sequence=1, path="synthetic.mov"):
    return {"offset": offset, "sequence": sequence, "path": path}
