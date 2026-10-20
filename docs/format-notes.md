# Format and reconstruction notes

The code handles a narrow fragmented ISO Base Media File Format layout seen in
some RØDECaster Video MOV files. It is intended to be inspected and adapted.

## Boxes and timing

- `ftyp` identifies the file family. An initialization `moov` holds track and
  codec configuration. A carved file may wrap that `moov` in an open-ended
  `mdat`; `init_header` exposes it by changing only the wrapper box header.
- `moof` contains `mfhd` sequence information and one `traf` per track.
  `tfdt` stores base decode time; `trun` stores sample counts, sizes, and
  sometimes durations. `tfhd` may contain an explicit base data offset.
- The following `mdat` contains encoded samples. A valid `moof` header alone
  does not prove that its full payload survived carving.
- A closing `moov` can contain movie duration and creation time. Those values
  help identify a session but do not replace the missing sample bytes.

The proof of concept assumes track 1 is HEVC video and track 2 AAC audio, both
with per-sample size information and an explicit `tfhd` base data offset. It
derives the video step from an opening fragment and compares each candidate's
decode time with `sequence × step`.

## Aligned block intrusion

Disk carving can return a large file whose apparent size is plausible while
extra allocation blocks interrupt encoded samples. For the supported HEVC
layout, `trun` gives each sample size. Many non-key samples begin with a
length-prefixed NAL marker. The repair routine searches for that marker at
candidate positions separated by a configurable allocation-block size. It
removes a block only when this yields a consistent `mdat` size and every later
sample marker is aligned.

This is not ordinary denoising. It reconstructs byte order only when the
original encoded bytes are still present. It does not guess a missing keyframe
or replace overwritten payloads.

## Audio and multiple angles

Camera and program files may contain matching fragment timelines while their
video payloads differ. Audio may be recoverable in another file even if one
camera's audio bytes are damaged. The rebuild tool can transplant a matching
audio `trun` and `tfdt` into the selected video fragment, then rewrite the
output offsets. Donor compatibility is structural; the result still requires
decoding and listening.

Each camera angle needs its own reconstructed output. Matching sequence and
time values are a prerequisite for synchronization, not proof that all files
belong to the same interview.

## Extension points

The parsers and repair logic are deliberately separate. Another recorder may
need different track IDs, codecs, timescales, fragment headers, sample markers,
allocation-unit sizes, or donor-selection rules. A general media-recovery
framework should add format-specific adapters and retain provenance for each
recovered sample rather than silently applying one device's assumptions.

