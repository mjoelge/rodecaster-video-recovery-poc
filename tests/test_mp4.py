import struct
import unittest

from rvc_poc.mp4 import (audio_signature, boxes, fragment, hybrid_fragment,
                         video_duration, video_sample_sizes)
from rvc_poc.rebuild import repair_fragment

from fixtures import box, make_fragment, row


class BoxTests(unittest.TestCase):
    def test_walk_normal_and_extended_boxes(self):
        normal = box(b"free", b"abcd")
        extended = struct.pack(">I4sQ", 1, b"wide", 20) + b"wxyz"
        self.assertEqual(list(boxes(normal + extended, 0, len(normal + extended))),
                         [(b"free", 0, 12), (b"wide", 12, 20)])

    def test_malformed_size_stops(self):
        bad = struct.pack(">I4s", 100, b"free") + b"a"
        self.assertEqual(list(boxes(bad, 0, len(bad))), [])

    def test_two_track_fragment(self):
        data, moof, _, _ = make_fragment()
        parsed = fragment(data, 0)
        self.assertEqual(parsed[0], 1)
        self.assertEqual(set(parsed[3]), {1, 2})
        self.assertEqual(parsed[3][1][1], 2)
        self.assertEqual(parsed[3][2][4], 8)
        self.assertEqual(video_duration(moof), 1024)
        self.assertEqual(video_sample_sizes(moof), [16, 16])

    def test_truncated_boxes_and_sample_table(self):
        data, moof, _, _ = make_fragment()
        self.assertIsNone(fragment(data[:len(moof) + 4], 0))
        with_durations, moof, _, _ = make_fragment(video_durations=(500, 524))
        self.assertEqual(video_duration(moof), 1024)
        self.assertEqual(video_sample_sizes(moof), [16, 16])
        shortened = bytearray(moof)
        trun = shortened.find(b"trun") - 4
        struct.pack_into(">I", shortened, trun + 12, 100)
        with self.assertRaises(ValueError):
            video_sample_sizes(shortened)
        with self.assertRaises(ValueError):
            video_duration(shortened)


class ReconstructionTests(unittest.TestCase):
    def test_uninterrupted_fragment(self):
        data, _, video, audio = make_fragment()
        repaired = repair_fragment(data, row())
        self.assertEqual(repaired["video"], video)
        self.assertEqual(repaired["audio"], audio)
        self.assertEqual(repaired["inserted_blocks_removed"], 0)

    def test_one_aligned_inserted_block(self):
        data, moof, video, audio = make_fragment()
        pad = b"P" * (-(len(moof) + 8) % 16)
        point = len(moof) + 8 + 16
        altered = pad + data[:point] + b"I" * 16 + data[point:]
        repaired = repair_fragment(altered, row(offset=len(pad)), block_size=16, max_blocks=1)
        self.assertEqual(repaired["video"], video)
        self.assertEqual(repaired["audio"], audio)
        self.assertEqual(repaired["inserted_blocks_removed"], 1)

    def test_missing_marker_and_out_of_bounds(self):
        data, moof, _, _ = make_fragment()
        marker = len(moof) + 8 + 16
        corrupted = data[:marker] + b"x" + data[marker + 1:]
        with self.assertRaisesRegex(ValueError, "marker missing"):
            repair_fragment(corrupted, row(), max_blocks=0)
        with self.assertRaisesRegex(ValueError, "extends beyond"):
            repair_fragment(data[:-2], row(), max_blocks=0)

    def test_hybrid_compatible_and_incompatible_audio(self):
        data, _, _, _ = make_fragment(audio_payload=b"abcdefgh")
        donor_data, _, _, _ = make_fragment(audio_payload=b"12345678")
        video = repair_fragment(data, row())
        donor = repair_fragment(donor_data, row())
        self.assertEqual(audio_signature(video), audio_signature(donor))
        output = hybrid_fragment(video, donor, 1000)
        self.assertIn(b"12345678", output)
        other_data, _, _, _ = make_fragment(audio_sizes=(3, 5))
        other = repair_fragment(other_data, row())
        with self.assertRaisesRegex(ValueError, "audio donor table differs"):
            hybrid_fragment(video, other, 1000)


if __name__ == "__main__":
    unittest.main()
