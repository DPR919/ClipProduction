import unittest

from bout_splitter.models import LightSample
from bout_splitter.video import build_video_phrase_clips, detect_light_events, parse_roi


class VideoAnalysisTests(unittest.TestCase):
    def test_parse_roi(self):
        roi = parse_roi("0.1,0.2,0.9,0.8")
        self.assertEqual((roi.x1, roi.y1, roi.x2, roi.y2), (0.1, 0.2, 0.9, 0.8))

    def test_detect_light_events_merges_active_samples(self):
        samples = [
            LightSample(1.0, 0, 0, 0, 0, 0, 0, 0.01),
            LightSample(2.0, 120, 0, 0, 0.1, 0, 0, 0.03),
            LightSample(2.2, 200, 0, 0, 0.2, 0, 0, 0.04),
            LightSample(5.0, 0, 150, 0, 0, 0.1, 0, 0.02),
        ]

        events = detect_light_events(samples, min_pixels=80, min_gap=1.0, colors={"red", "green"})

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].color, "red")
        self.assertEqual(events[0].peak_time, 2.2)
        self.assertEqual(events[1].color, "green")

    def test_build_video_phrase_clips_uses_light_event_and_motion_lookback(self):
        samples = [
            LightSample(9.0, 0, 0, 0, 0, 0, 0, 0.05),
            LightSample(10.0, 0, 0, 0, 0, 0, 0, 0.004),
            LightSample(11.0, 0, 0, 0, 0, 0, 0, 0.04),
            LightSample(12.0, 200, 0, 0, 0.2, 0, 0, 0.08),
        ]
        events = detect_light_events(samples, min_pixels=80)

        clips = build_video_phrase_clips(
            events,
            samples,
            lookback=5.0,
            pre_roll=0.5,
            post_roll=1.5,
            motion_threshold=0.01,
        )

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].clip_start, 9.5)
        self.assertEqual(clips[0].clip_end, 13.5)


if __name__ == "__main__":
    unittest.main()
