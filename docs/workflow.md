# Recovery workflow

This is a generalized account of the investigation and reconstruction process.
It intentionally contains no real recording details.

## 1. Preserve and inventory

Unmount or stop using the formatted recording disk. Do not run recovery tools
that write to it. Record capacity and filesystem information. Create a full
image if storage permits, keeping a checksum and a work log outside the public
repository. If imaging is impractical, restrict raw reads to known byte ranges
and capture them to another physical disk. The raw-device tools reject a
destination when they cannot establish that separation. On Linux this includes
mapped, RAID, loop, overlay, and unresolved device layouts. With a regular
disk image, the operator must establish physical separation from the original
disk independently.

Carve with PhotoRec or a comparable tool. Keep its DFXML mapping when available:
it can map a carved file offset to an original physical disk offset. Do not
discard files classified as “other”; they may be sidecars, index remnants, or
files with a misleading extension. Classify by signature and parsed structure.

## 2. Learn from intact files

Inspect at least one playable recording from the same recorder and settings.
Map its `ftyp`, initialization `moov`, repeated `moof`/`mdat` pairs, and any
closing `moov`. Determine track IDs, codec configuration, video timescale,
fragment duration, sample count, and audio timing. Compare more than one intact
file before treating a pattern as stable.

Do not copy an intact file's index into another recording. An index refers to
specific byte offsets and sample tables. It can serve as a structural template
only after the target's actual fragments have been located and validated.

## 3. Catalog and group

Use `rvc-catalog` on the carved tree. It records each plausible fragment's
sequence, decode time, sample counts, payload sizes, and optional disk offset.
Use `rvc-scan` on an image to locate `mvhd` metadata and fragment regions
without relying on PhotoRec filenames.

Group candidate files using several independent signals:

| Signal | Use | Limitation |
| --- | --- | --- |
| `mvhd` creation time | Narrow candidate sessions | Recorder clock can be wrong or header absent |
| `mfhd` sequence and `tfdt` time | Reconstruct relative timeline | Separate sessions can reuse sequence zero |
| Matching audio time/sample count | Pair camera and audio fragments | Must still decode and listen |
| Original disk position | Find adjacent source regions | Allocation can be interleaved |
| Frame and sound review | Confirm people and scene | A matching studio alone is insufficient |

The CSV outputs are private working records. They can reveal recording dates,
file paths, and physical layout; keep them outside this checkout.

## 4. Reconstruct conservatively

Pick a file with an intact initialization header and opening fragment. Run
`rvc-rebuild` with a new output path. It uses the opening fragment's video
duration to select a consistent sequence timeline. When an aligned extra block
interrupts `mdat`, it follows per-sample sizes and HEVC NAL markers to locate
the boundary. It removes only blocks supported by those checks.

A companion file can donate audio when its sequence, decode time, sample
count, byte count, and audio table structure match the selected video
fragment. More than one distinct compatible donor is ambiguous, so that
sequence is skipped. These checks do not establish session identity: supply
only sources already grouped as one recording. Build each camera angle
separately; synchronized angles are separate outputs. The local manifest lists
source selections, rejected or ambiguous donors, and holes. If a fragment
fails validation, leave a gap rather than inventing bytes.

The current proof of concept does not reconstruct an arbitrary missing `moov`,
repair every possible displaced opening, or choose the correct camera on visual
grounds. Those decisions need additional evidence and case-specific code.

## 5. Recover from original disk regions

If the carved output ends early, inspect its last valid sequence and original
disk offset. Use a raw scan to locate later fragments or a closing index.
Confirm the source device's capacity and an independent anchor before copying
a byte range. Check neighboring recording boundaries so the capture does not
silently merge separate sessions. `rvc-capture` records a bounded region to a
new file; it does not modify the source. If copying is interrupted, a partial
new file may remain and must be reviewed or removed before retrying. The same
applies to reconstructed outputs.

## 6. Review the result

Use ffprobe to inspect duration, streams, and timestamps. Decode representative
frames near the start, middle, gap boundaries, and end. Listen to corresponding
audio. Count missing fragment sequences and compare expected duration to the
actual scene timeline. A file can report the expected duration while containing
frozen video or silent/mismatched audio.

Keep all originals, catalogs, manifests, and captured regions in private
working storage. Only manually reviewed, non-sensitive code and prose belong
in a shared repository.

## Synthetic example

Suppose a hypothetical 30-minute recording contains fragments numbered 0–1799.
PhotoRec recovers sequence 0–399 in one file and 900–1799 in another. A rebuilt
MOV can preserve both spans, but sequences 400–899 remain missing. If a read-only
disk scan finds those fragments in a separate raw region, capture and catalog
that region before rebuilding. If the region has been overwritten, a 30-minute
index cannot recreate the missing 500 seconds of picture or sound.

