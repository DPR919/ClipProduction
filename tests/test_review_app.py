from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bout_splitter.review_app import ReviewManager, make_handler, validate_review, validate_settings
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

    def test_http_range_and_review_request(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.manager))
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
        self.manager._write(job)
        clip = SimpleNamespace(index=1, clip_start=1.25, clip_end=3.5, event_time=3.0,
                               start_method="sustained_setup_estimate", review_reasons=[])

        def fake_cut(source, start, end, output, *, reencode):
            self.assertTrue(reencode)
            self.assertEqual((start, end), (1.25, 3.5))
            output.write_bytes(b"mp4")

        with patch("bout_splitter.review_app.duration_seconds", return_value=10), \
             patch("bout_splitter.review_app.sample_video_lights", return_value=[]), \
             patch("bout_splitter.review_app.detect_light_events", return_value=[]), \
             patch("bout_splitter.review_app.build_video_phrase_clips", return_value=[clip]), \
             patch("bout_splitter.review_app.write_light_analysis"), \
             patch("bout_splitter.review_app.cut_time_range", side_effect=fake_cut):
            self.manager._generate(self.job_id)
        ready = self.manager.get(self.job_id)
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["clips"][0]["decision"], "pending")
        self.assertEqual(self.manager.clip_path(self.job_id, 1).read_bytes(), b"mp4")


class UploadClientTests(unittest.TestCase):
    def test_site_url_requires_https_except_localhost(self):
        self.assertEqual(normalize_site_url("http://localhost:3000/"), "http://localhost:3000")
        with self.assertRaises(ValueError):
            normalize_site_url("http://example.com")
        with self.assertRaises(ValueError):
            normalize_site_url("https://example.com/api")


if __name__ == "__main__":
    unittest.main()
