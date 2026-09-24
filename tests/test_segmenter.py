import unittest

from bout_splitter.models import Signal
from bout_splitter.segmenter import pair_phrases


class SegmenterTests(unittest.TestCase):
    def test_pair_phrases_uses_en_garde_minus_preroll_and_halt_plus_postroll(self):
        clips = pair_phrases(
            [
                Signal(kind="en_garde", time=10.0),
                Signal(kind="allez", time=12.0),
                Signal(kind="halt", time=15.0),
            ],
            pre_roll=0.5,
            post_roll=2.0,
            media_duration=20.0,
        )

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].clip_start, 9.5)
        self.assertEqual(clips[0].clip_end, 17.0)
        self.assertEqual(clips[0].action_start, 12.0)

    def test_pair_phrases_ignores_halt_without_en_garde(self):
        clips = pair_phrases([Signal(kind="halt", time=15.0)])
        self.assertEqual(clips, [])


if __name__ == "__main__":
    unittest.main()
