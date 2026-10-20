import unittest

from rvc_poc.rebuild import repair_fragment, select_audio_donor

from fixtures import make_fragment, row


class DonorTests(unittest.TestCase):
    def setUp(self):
        source, _, _, _ = make_fragment(audio_payload=b"abcdefgh")
        self.video = repair_fragment(source, row())

    def candidate(self, name, payload=b"abcdefgh", audio_sizes=(4, 4)):
        data, _, _, _ = make_fragment(audio_payload=payload, audio_sizes=audio_sizes)
        metadata = row(path=name)
        return metadata, repair_fragment(data, metadata)

    def test_one_compatible_candidate(self):
        candidate = self.candidate("synthetic_a.mov")
        selected, rejected, ambiguous = select_audio_donor(self.video, [candidate])
        self.assertEqual(selected[0], candidate[0])
        self.assertEqual(rejected, [])
        self.assertEqual(ambiguous, [])

    def test_exact_duplicates_collapse(self):
        a = self.candidate("synthetic_a.mov")
        b = self.candidate("synthetic_b.mov")
        selected, rejected, ambiguous = select_audio_donor(self.video, [a, b])
        self.assertEqual(selected[0], a[0])
        self.assertEqual(rejected, [])
        self.assertEqual(ambiguous, [])

    def test_incompatible_candidate_rejected(self):
        other = self.candidate("synthetic_other.mov", audio_sizes=(3, 5))
        selected, rejected, ambiguous = select_audio_donor(self.video, [other])
        self.assertIsNone(selected)
        self.assertEqual(rejected[0]["path"], "synthetic_other.mov")
        self.assertEqual(ambiguous, [])

    def test_distinct_compatible_candidates_are_ambiguous(self):
        a = self.candidate("synthetic_a.mov", b"abcdefgh")
        b = self.candidate("synthetic_b.mov", b"12345678")
        selected, rejected, ambiguous = select_audio_donor(self.video, [a, b])
        self.assertIsNone(selected)
        self.assertEqual(rejected, [])
        self.assertEqual({entry["path"] for entry in ambiguous},
                         {"synthetic_a.mov", "synthetic_b.mov"})


if __name__ == "__main__":
    unittest.main()
