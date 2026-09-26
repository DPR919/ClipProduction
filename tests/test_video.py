import unittest

from bout_splitter.models import LightEvent, LightSample
from bout_splitter.video import build_video_phrase_clips, detect_light_events, find_setup_start, parse_roi


class VideoAnalysisTests(unittest.TestCase):
    @staticmethod
    def sample(time, motion):
        return LightSample(time, 0, 0, 0, 0, 0, 0, motion)

    def test_short_dip_during_attack_does_not_replace_setup(self):
        motions = [0.01] * 5 + [0.08] * 4 + [0.01] + [0.08] * 4
        samples = [self.sample(10 + i * 0.2, m) for i, m in enumerate(motions)]
        self.assertEqual(find_setup_start(samples, 0.02), 10)

    def test_camera_cut_does_not_confirm_action(self):
        motions = [0.01] * 5 + [0.4] + [0.08] * 4
        samples = [self.sample(10 + i * 0.2, m) for i, m in enumerate(motions)]
        self.assertIsNone(find_setup_start(samples, 0.02))

    def test_missing_samples_do_not_form_continuous_setup(self):
        samples = [self.sample(10, 0.01), self.sample(12, 0.01)]
        samples += [self.sample(12.2 + i * 0.2, 0.08) for i in range(6)]
        self.assertIsNone(find_setup_start(samples, 0.02))

    def test_previous_event_bounds_lookback_and_end_uses_onset(self):
        events = [
            LightEvent(1, 191, 192.8, 192.6, "red", 2000, 0, 0, 1),
            LightEvent(2, 201.6, 203.4, 203.4, "red", 2000, 0, 0, 1),
        ]
        clips = build_video_phrase_clips(events, [], lookback=20, pre_roll=5)
        self.assertEqual(clips[1].clip_start, 192.8)
        self.assertAlmostEqual(clips[1].clip_end, 203.6)
        self.assertEqual(clips[1].event_time, 201.6)
        self.assertEqual(clips[1].start_method, "lookback_fallback")
        self.assertTrue(clips[1].review_reasons)

    def test_postroll_cannot_include_next_light_activation(self):
        events = [
            LightEvent(1, 10, 11, 10.8, "red", 2000, 0, 0, 1),
            LightEvent(2, 13, 14, 13.8, "red", 2000, 0, 0, 1),
        ]
        clips = build_video_phrase_clips(events, [], post_roll=5, media_duration=15)
        self.assertEqual(clips[0].clip_end, 13)
        self.assertEqual(clips[1].clip_end, 15)

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
