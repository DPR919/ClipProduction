import subprocess
import tempfile
import unittest
from pathlib import Path

from bout_splitter.cutter import cut_video_phrase_clips
from bout_splitter.media import MediaToolError, find_media_tool, probe_video
from bout_splitter.models import LightEvent, VideoPhraseClip


class AccurateCutTests(unittest.TestCase):
    def test_default_cut_excludes_previous_keyframe_video(self):
        try:
            import av
            ffmpeg = find_media_tool("ffmpeg")
            find_media_tool("ffprobe")
        except (ImportError, MediaToolError) as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "long_gop.mp4"
            subprocess.run([
                ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=160x90:rate=25:duration=7",
                "-c:v", "libx264", "-g", "150", "-keyint_min", "150",
                "-sc_threshold", "0", str(source),
            ], check=True, capture_output=True)
            event = LightEvent(1, 5, 5.4, 5.2, "red", 1000, 0, 0, 1)
            clip = VideoPhraseClip(1, 4.2, 6.2, 5, "red", event,
                                   start_method="sustained_setup_estimate",
                                   review_reasons=["En garde timestamp is unverified"])
            result = cut_video_phrase_clips(source, [clip], Path(temp) / "out")[0]
            self.assertEqual(result.start_method, clip.start_method)
            self.assertEqual(result.review_reasons, clip.review_reasons)
            with av.open(result.output) as container:
                times = [float(p.pts * p.time_base) for p in container.demux(video=0)
                         if p.pts is not None]
            # Stream-copy would retain the keyframe from 0s with a negative PTS.
            self.assertGreaterEqual(min(times), 0)
            self.assertAlmostEqual(float(probe_video(result.output)["format"]["duration"]), 2, delta=0.08)


if __name__ == "__main__":
    unittest.main()
