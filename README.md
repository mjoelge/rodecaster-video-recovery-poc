# Fragmented MOV recovery proof of concept

This repository documents a practical workflow for recovering RØDECaster Video
recordings from a formatted disk. The tools also form a starting point for
other fragmented MP4/MOV recovery work. They inspect the source read only and
write new catalogs, captures, and reconstructed files to a separate location.

**Experimental recovery software:** this is not a universal repair utility. A plausible duration does not prove that picture
and sound are complete or correct.


## What the proof of concept does

- Catalogs `moof`/`mdat` fragments in carved MOV/MP4 files and optionally maps
  their positions back to an original disk with PhotoRec DFXML.
- Scans a disk image or read-only raw disk for `mvhd` movie headers and long
  fragment timelines, including fragments whose closing index is missing.
- Captures a verified byte range from an original disk to another disk.
- Reassembles compatible HEVC video and AAC audio fragments into a new
  fragmented MOV. It can remove aligned allocation blocks that interrupt
  video samples and use audio from a matching companion file.
- Records gaps and donor choices in a local manifest for human review.

These are conservative tools for one observed **two-track** layout. A movie
that uses different track IDs, sample tables, codecs, or corruption patterns
needs an adapter. A decoded duration alone does not prove that every frame or
audio sample is correct.

## Setup

Python 3.10 or newer is required. FFmpeg and ffprobe are useful for reviewing
outputs; PhotoRec is an optional source of carved files and DFXML.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

All paths below are illustrative. Put the working directory and output on a
**different physical disk** from the formatted source.

```sh
rvc-catalog /path/to/carved-files \
  --dfxml /path/to/photorec-report.xml \
  --out /path/to/work/fragment-catalog.sqlite

rvc-scan /path/to/source-disk.img \
  --out-dir /path/to/work/disk-scan \
  --start-byte 0 --length-byte 100000000000

rvc-rebuild --video /path/to/carved/program.mov \
  --audio /path/to/carved/matching-audio.mov \
  --output /path/to/work/program-rebuilt.mov

ffprobe -v error -show_format -show_streams /path/to/work/program-rebuilt.mov
```

`rvc-rebuild` leaves every input untouched and refuses an existing output.
Use `--max-sequence` to constrain a session, and `--min-video-bytes` or
`--max-video-bytes` when multiple video streams share a carved file. The
manifest reports missing sequences. Source priority follows the order of
`--video` arguments; each camera angle should normally be rebuilt separately.
If no fragment passes repair, the empty media output is removed and the
diagnostic manifest remains for review.
Without `--audio`, the selected video fragment supplies its own audio.
Explicit donors must match its audio time, sample count, payload byte count,
and table structure. Distinct compatible donors are reported as ambiguous;
that sequence is skipped. **Structural compatibility does not establish
recording identity.** Group inputs by session before rebuilding and review the
sound afterward.

`rvc-capture` needs a known source capacity and the SHA-256 of a **4096-byte
anchor**, established independently from a carved file or an earlier disk
image. This prevents a raw capture from silently reading the wrong disk:

```sh
rvc-capture /path/to/source-disk.img /path/to/work/region.img \
  --start-byte START --length-byte LENGTH \
  --expected-size CAPACITY \
  --anchor-offset KNOWN_OFFSET --anchor-sha256 KNOWN_SHA256
```

The range and anchor offset must be sector aligned. On macOS, raw access is
limited to an external whole disk, and `diskutil` must identify a separate
destination disk. On Linux, `lsblk` must resolve the raw source and destination
to different physical disks. Simple disks and partitions are supported;
device-mapper, RAID, loop, overlay, and unresolved topologies are rejected.
For a **regular disk image**, the tools cannot infer whether its destination
is separate from the original physical disk; choosing safe storage remains
your responsibility. Sources are opened read only. Raw access may require
administrator privileges; enter credentials only in your local terminal.

An interrupted capture or rebuild can leave a partial new output. Review or
remove it before retrying; the commands refuse to overwrite an existing
output or manifest.

## Method

1. Stop writing to the source disk. Record its capacity and make an image when
   possible. Work from the image or use read-only raw reads.
2. Carve candidate files with PhotoRec, keeping its DFXML report if available.
   Sort candidates by actual file signature and container structure, not just
   extension. Keep non-video files: sidecars and indexes can hold clues.
3. Catalog fragments, headers, sequence numbers, decode times, sample counts,
   and original disk positions. Compare intact recordings to damaged ones.
4. Group files by movie creation time, fragment timeline, matching audio times,
   physical disk order, and visual/audio content. A timestamp is a clue, not
   proof of session identity.
5. Rebuild a new file from valid fragments. Detect and remove inserted blocks
   only where sample tables and HEVC markers support that repair. If video and
   audio survive in different carved files, combine matching fragments.
6. Search the original disk for missing regions and closing indexes. Capture
   only identified regions after checking source identity and boundaries.
7. Review every recovered angle and audio track. Compare expected duration,
   decoded frames, audio continuity, sequence gaps, and scene content. Keep the
   original carved files and raw captures unchanged.

See [the detailed workflow](docs/workflow.md),
[format notes](docs/format-notes.md), and
[privacy guidance](docs/privacy.md).

## Limits

PhotoRec can recover plausible file sizes while interleaving unrelated
allocation blocks or omitting later fragments. A short playable output may
therefore be a valid opening with most of the recording absent. The tools do
not synthesize missing media, infer speech from noise, or restore bytes that
have been overwritten. A complete reconstruction requires source fragments
or an independent copy of those bytes.

The project is a proof of concept, not a universal repair utility. It makes
its assumptions explicit so other containers, codecs, and disk layouts can be
added without hiding uncertain repairs.

This independent project is not affiliated with or endorsed by RØDE. RØDE
and RØDECaster are trademarks of their respective owner.

After the editable install in Setup, run the synthetic suite with
`python -m unittest discover -s tests -v`. The tests need no media, attached
disk, network access, or administrator privileges.

Made with ChatGPT.
