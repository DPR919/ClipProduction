from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bout_splitter.models import LightSample
from bout_splitter.media import MediaToolError, duration_seconds, find_media_tool
from bout_splitter.review_app import (
    LocalReviewServer, ReviewManager, make_handler, validate_review, validate_settings, validate_title_prefix,
)
from bout_splitter.upload_client import UploadError, normalize_site_url


class FakeClient:
    site_url = "https://example.com"

    def __init__(self):
        self.calls = []
        self.fail_register = False

    def presign(self, file):
        self.calls.append(("presign", file.name))
        return "https://bucket.s3.amazonaws.com/key", "clips/key"

    def put_video(self, url, file):
        self.calls.append(("put", file.name))

    def register(self, key, metadata):
        self.calls.append(("register", key, metadata.copy()))
        if self.fail_register:
            raise UploadError("Temporary registration failure")
        return "remote-id"


class ReviewAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = ReviewManager(Path(self.temp.name))
        with patch("bout_splitter.review_app.threading.Thread.start"):
            job = self.manager.create_from_stream("example.mp4", 4, io.BytesIO(b"test"), validate_settings({}))
        job["state"] = "ready"
        job["phase"] = "Review clips"
        job["clips"] = [
            {"index": 1, "file": "phrase_001.mp4", "clip_start": 1, "clip_end": 3,
             "decision": "pending", "title": "Phrase 001", "scoreAtTouch": "", "notes": "",
             "upload_status": "pending", "s3_key": None, "remote_clip_id": None, "upload_error": None},
            {"index": 2, "file": "phrase_002.mp4", "clip_start": 4, "clip_end": 6,
             "decision": "pending", "title": "Phrase 002", "scoreAtTouch": "", "notes": "",
             "upload_status": "pending", "s3_key": None, "remote_clip_id": None, "upload_error": None},
        ]
        self.job_id = job["id"]
        self.manager._write(job)
        clips_dir = Path(self.temp.name) / self.job_id / "clips"
        clips_dir.mkdir()
        (clips_dir / "phrase_001.mp4").write_bytes(b"0123456789")
        (clips_dir / "phrase_002.mp4").write_bytes(b"abcdefghij")

    def _review_all(self):
        self.manager.review(self.job_id, {"index": 1, "decision": "keep", "title": "First touch", "scoreAtTouch": "8-7"})
        self.manager.review(self.job_id, {"index": 2, "decision": "discard", "title": "Second", "scoreAtTouch": ""})
        self.manager.set_shared(self.job_id, {"eventName": "Grand Prix", "leftFencer": "Left",
            "rightFencer": "Right", "weapon": "sabre", "sourceUrl": "https://example.com/bout"})

    def test_review_validation_and_only_accepted_clip_uploads(self):
        with self.assertRaisesRegex(ValueError, "Review every clip"):
            validate_review(self.manager.get(self.job_id))
        self._review_all()
        client = FakeClient()
        self.manager.client = client
        with patch("bout_splitter.review_app.threading.Thread.start"):
            self.manager.start_upload(self.job_id)
        self.manager._upload(self.job_id, client)
        job = self.manager.get(self.job_id)
        self.assertEqual(job["upload_state"], "done")
        self.assertEqual(job["clips"][0]["remote_clip_id"], "remote-id")
        self.assertEqual(job["clips"][1]["upload_status"], "pending")
        self.assertEqual([call[0] for call in client.calls], ["presign", "put", "register"])
        self.assertEqual(client.calls[2][2]["scoreAtTouch"], "8-7")

    def test_register_retry_reuses_uploaded_s3_key(self):
        self._review_all()
        client = FakeClient()
        client.fail_register = True
        self.manager.client = client
        with patch("bout_splitter.review_app.threading.Thread.start"):
            self.manager.start_upload(self.job_id)
        self.manager._upload(self.job_id, client)
        self.assertEqual(self.manager.get(self.job_id)["clips"][0]["s3_key"], "clips/key")
        client.fail_register = False
        with patch("bout_splitter.review_app.threading.Thread.start"):
            self.manager.start_upload(self.job_id)
        self.manager._upload(self.job_id, client)
        self.assertEqual([call[0] for call in client.calls], ["presign", "put", "register", "register"])

    def test_crop_exports_from_original_and_uploads_edited_file(self):
        self._review_all()
        original = self.manager.clip_path(self.job_id, 1)
        cuts = []

        def fake_cut(source, start, end, output, *, reencode):
            cuts.append((source, start, end, reencode))
            output.write_bytes(b"cropped")

        with patch("bout_splitter.review_app.duration_seconds", return_value=2.0), \
             patch("bout_splitter.review_app.cut_time_range", side_effect=fake_cut):
            job = self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.25, "end": 1.75})
            first_edit = Path(self.temp.name) / self.job_id / "edited" / job["clips"][0]["trim_file"]
            job = self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.5, "end": 1.5})
        self.assertEqual(cuts, [(original, 0.25, 1.75, True), (original, 0.5, 1.5, True)])
        self.assertEqual(original.read_bytes(), b"0123456789")
        edited = Path(self.temp.name) / self.job_id / "edited" / job["clips"][0]["trim_file"]
        self.assertEqual(edited.read_bytes(), b"cropped")
        self.assertFalse(first_edit.exists())
        client = FakeClient()
        self.manager.client = client
        with patch("bout_splitter.review_app.threading.Thread.start"):
            self.manager.start_upload(self.job_id)
        self.manager._upload(self.job_id, client)
        self.assertEqual(client.calls[0], ("presign", edited.name))
        self.assertEqual(client.calls[1], ("put", edited.name))
        with self.assertRaisesRegex(ValueError, "already sent to S3"):
            self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0, "end": 2})

    def test_crop_validation_reset_and_failed_export(self):
        with patch("bout_splitter.review_app.duration_seconds", return_value=2.0):
            for begin, end in [(-1, 1), (0.5, 0.55), (0, 3), (float("nan"), 1)]:
                with self.assertRaises(ValueError):
                    self.manager.trim_clip(self.job_id, {"index": 1, "begin": begin, "end": end})
            with patch("bout_splitter.review_app.cut_time_range", side_effect=RuntimeError("ffmpeg failed")):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.2, "end": 1.8})
            self.assertNotIn("trim_file", self.manager.get(self.job_id)["clips"][0])
            with patch("bout_splitter.review_app.cut_time_range", side_effect=lambda a,b,c,d,*,reencode: d.write_bytes(b"crop")):
                cropped = self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.2, "end": 1.8})
            edited = Path(self.temp.name) / self.job_id / "edited" / cropped["clips"][0]["trim_file"]
            reset = self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0, "end": 2})
        self.assertIsNone(reset["clips"][0]["trim_file"])
        self.assertFalse(edited.exists())
        self.assertEqual((reset["clips"][0]["trim_begin"], reset["clips"][0]["trim_end"]), (0, 2))

    def test_crop_locked_after_s3_put_even_if_registration_failed(self):
        job = self.manager.get(self.job_id)
        job["clips"][0]["s3_key"] = "clips/key"
        job["clips"][0]["upload_status"] = "failed"
        self.manager._write(job)
        with self.assertRaisesRegex(ValueError, "already sent to S3"):
            self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.2, "end": 1.8})

    def test_real_crop_has_selected_duration(self):
        try:
            ffmpeg = find_media_tool("ffmpeg")
            find_media_tool("ffprobe")
        except MediaToolError as exc:
            self.skipTest(str(exc))
        original = self.manager.clip_path(self.job_id, 1)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=25:duration=3", "-c:v", "libx264",
                        str(original)], check=True, capture_output=True)
        job = self.manager.trim_clip(self.job_id, {"index": 1, "begin": 0.4, "end": 1.6})
        edited = Path(self.temp.name) / self.job_id / "edited" / job["clips"][0]["trim_file"]
        self.assertAlmostEqual(duration_seconds(edited), 1.2, delta=0.08)
        self.assertAlmostEqual(duration_seconds(original), 3, delta=0.08)

    def test_http_range_and_review_request(self):
        server = LocalReviewServer(("127.0.0.1", 0), make_handler(self.manager))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = HTTPConnection("127.0.0.1", server.server_port)
        self.addCleanup(connection.close)
        connection.request("GET", f"/media/{self.job_id}/1", headers={"Range": "bytes=2-5"})
        response = connection.getresponse()
        self.assertEqual(response.status, 206)
        self.assertEqual(response.getheader("Content-Range"), "bytes 2-5/10")
        self.assertEqual(response.read(), b"2345")
        payload = json.dumps({"index": 1, "decision": "keep", "title": "A", "scoreAtTouch": "1-0"})
        connection.request("POST", f"/api/jobs/{self.job_id}/review", payload,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.request("POST", f"/api/jobs/{self.job_id}/review", payload,
                           headers={"Content-Type": "application/json", "X-Local-Request": "review-ui"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.load(response)["job"]["clips"][0]["decision"], "keep")
        with patch.object(self.manager, "_generate"):
            connection.request("POST", "/api/jobs?name=another.mp4&titlePrefix=Touch%20point&settings=%7B%7D",
                               b"test", headers={"X-Local-Request": "review-ui"})
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            self.assertEqual(json.load(response)["job"]["title_prefix"], "Touch point")
        with patch("bout_splitter.review_app.duration_seconds", return_value=2.0), \
             patch("bout_splitter.review_app.cut_time_range", side_effect=lambda a,b,c,d,*,reencode: d.write_bytes(b"crop")):
            connection.request("POST", f"/api/jobs/{self.job_id}/trim",
                               json.dumps({"index": 1, "begin": 0.25, "end": 1.5}),
                               headers={"Content-Type": "application/json", "X-Local-Request": "review-ui"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["job"]["clips"][0]["trim_begin"], 0.25)

    def test_local_server_refuses_duplicate_port(self):
        server = LocalReviewServer(("127.0.0.1", 0), make_handler(self.manager))
        self.addCleanup(server.server_close)
        with self.assertRaises(OSError):
            LocalReviewServer(("127.0.0.1", server.server_port), make_handler(self.manager))

    def test_interrupted_upload_is_retryable(self):
        job = self.manager.get(self.job_id)
        job["upload_state"] = "running"
        job["clips"][0]["upload_status"] = "registering"
        job["clips"][0]["s3_key"] = "clips/key"
        self.manager._write(job)
        restored = ReviewManager(Path(self.temp.name)).get(self.job_id)
        self.assertEqual(restored["upload_state"], "partial")
        self.assertEqual(restored["clips"][0]["upload_status"], "failed")
        self.assertEqual(restored["clips"][0]["s3_key"], "clips/key")

    def test_generation_creates_reviewable_clip(self):
        job = self.manager.get(self.job_id)
        job["state"] = "processing"
        job["clips"] = []
        job["title_prefix"] = "Touch"
        self.manager._write(job)
        clip = SimpleNamespace(index=1, clip_start=1.25, clip_end=3.5, event_time=3.0,
                               start_method="sustained_setup_estimate", review_reasons=[])

        def fake_cut(source, start, end, output, *, reencode):
            self.assertTrue(reencode)
            self.assertEqual((start, end), (1.25, 3.5))
            output.write_bytes(b"mp4")

        def fake_analysis(path, **kwargs):
            path.write_text(json.dumps({"settings": {}}), encoding="utf-8")

        with patch("bout_splitter.review_app.duration_seconds", return_value=10), \
             patch("bout_splitter.review_app.sample_video_lights", return_value=[]), \
             patch("bout_splitter.review_app.detect_light_onsets", return_value=[]), \
             patch("bout_splitter.review_app.build_video_phrase_clips", return_value=[clip]), \
             patch("bout_splitter.review_app.write_light_analysis", side_effect=fake_analysis), \
             patch("bout_splitter.review_app.cut_time_range", side_effect=fake_cut):
            self.manager._generate(self.job_id)
        ready = self.manager.get(self.job_id)
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["clips"][0]["decision"], "pending")
        self.assertEqual(ready["clips"][0]["title"], "Touch 001")
        self.assertEqual(self.manager.clip_path(self.job_id, 1).read_bytes(), b"mp4")

    def test_reanalysis_preserves_review_and_reuses_source(self):
        self._review_all()
        original = self.manager.get(self.job_id)
        original["title_prefix"] = "Hit"
        self.manager._write(original)
        settings = validate_settings({"referenceAt": "7:00", "minGap": 1.5})
        with patch("bout_splitter.review_app.threading.Thread.start"):
            new_job = self.manager.reanalyze(self.job_id, settings)
            renamed_job = self.manager.reanalyze(self.job_id, settings, "Exchange")
        self.assertNotEqual(new_job["id"], self.job_id)
        self.assertEqual(new_job["settings"]["reference_at"], 420)
        self.assertEqual(new_job["settings"]["min_gap"], 1.5)
        self.assertEqual(new_job["title_prefix"], "Hit")
        self.assertEqual(renamed_job["title_prefix"], "Exchange")
        self.assertEqual(new_job["state"], "processing")
        self.assertEqual(self.manager.source_path(new_job["id"]).read_bytes(), b"test")
        self.assertEqual(self.manager.get(self.job_id)["clips"][0]["decision"], "keep")

    def test_title_prefix_defaults_and_validation(self):
        self.assertEqual(self.manager.get(self.job_id)["title_prefix"], "Phrase")
        self.assertEqual(validate_title_prefix("  Touch  "), "Touch")
        for value in ("", "  ", "X" * 101, "Touch\nnext", None):
            with self.assertRaises(ValueError):
                validate_title_prefix(value)
        with patch("bout_splitter.review_app.threading.Thread.start"):
            new_job = self.manager.create_from_stream(
                "custom.mp4", 4, io.BytesIO(b"test"), validate_settings({}), "Exchange",
            )
        self.assertEqual(self.manager.get(new_job["id"])["title_prefix"], "Exchange")

    def test_settings_accept_color_regions_and_known_touch(self):
        settings = validate_settings({"referenceAt": "7:00", "redRoi": "0.08,0.74,0.47,0.9",
                                      "greenRoi": "0.53,0.74,0.92,0.9"})
        self.assertEqual(settings["reference_at"], 420)
        self.assertEqual(settings["red_roi"].x1, 0.08)
        self.assertEqual(settings["green_roi"].x1, 0.53)

    def test_pervasive_graphics_require_calibration(self):
        job = self.manager.get(self.job_id)
        job["state"] = "processing"
        self.manager._write(job)
        samples = [LightSample(i * 0.2, 900, 0, 0, 0, 0, 0, 0) for i in range(30)]
        with patch("bout_splitter.review_app.duration_seconds", return_value=10), \
             patch("bout_splitter.review_app.sample_video_lights", return_value=samples):
            self.manager._generate(self.job_id)
        failed = self.manager.get(self.job_id)
        self.assertEqual(failed["state"], "error")
        self.assertIn("Colored graphics", failed["error"])


class UploadClientTests(unittest.TestCase):
    def test_site_url_requires_https_except_localhost(self):
        self.assertEqual(normalize_site_url("http://localhost:3000/"), "http://localhost:3000")
        with self.assertRaises(ValueError):
            normalize_site_url("http://example.com")
        with self.assertRaises(ValueError):
            normalize_site_url("https://example.com/api")


if __name__ == "__main__":
    unittest.main()
