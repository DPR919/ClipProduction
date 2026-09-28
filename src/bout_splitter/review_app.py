"""Local review UI for generating and uploading fencing phrase clips."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import threading
import uuid
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cutter import cut_time_range
from .media import duration_seconds
from .upload_client import UploadError, WhatsthecallClient, normalize_site_url
from .video import (
    Roi,
    build_video_phrase_clips,
    calibrate_light_threshold,
    detect_light_onsets,
    parse_roi,
    sample_video_lights,
    write_light_analysis,
)

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class LocalReviewServer(ThreadingHTTPServer):
    allow_reuse_address = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_settings(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Invalid analysis settings.")
    fps = float(data.get("fps", 5))
    min_pixels = int(data.get("minPixels", 500))
    min_gap = float(data.get("minGap", 1.5))
    lookback = float(data.get("lookback", 8))
    start_at = float(data.get("startAt", 0))
    end_at = data.get("endAt")
    end_at = float(end_at) if end_at not in (None, "") else None
    reference_at = data.get("referenceAt")
    if isinstance(reference_at, str) and ":" in reference_at:
        parts = reference_at.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("Known touch time must be seconds or mm:ss.")
        reference_at = int(parts[0]) * 60 + int(parts[1])
    reference_at = float(reference_at) if reference_at not in (None, "") else None
    if not (1 <= fps <= 20 and 1 <= min_pixels <= 100000 and 0 <= min_gap <= 20):
        raise ValueError("Invalid sample rate, light threshold, or merge gap.")
    if not (1 <= lookback <= 30 and 0 <= start_at and (end_at is None or end_at > start_at)):
        raise ValueError("Invalid clip lookback or bout time range.")
    if reference_at is not None and (reference_at < start_at or (end_at is not None and reference_at > end_at)):
        raise ValueError("Known touch must be inside the selected bout time range.")
    roi = parse_roi(str(data.get("roi", "0,0.75,1,0.9")))
    red_roi = parse_roi(str(data["redRoi"])) if data.get("redRoi") else None
    green_roi = parse_roi(str(data["greenRoi"])) if data.get("greenRoi") else None
    motion_roi = parse_roi(str(data.get("motionRoi", "0,0,1,0.78")))
    return {
        "fps": fps, "min_pixels": min_pixels, "min_gap": min_gap,
        "lookback": lookback, "start_at": start_at, "end_at": end_at,
        "reference_at": reference_at,
        "roi": roi, "red_roi": red_roi, "green_roi": green_roi,
        "motion_roi": motion_roi,
    }


def validate_title_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a clip title prefix of up to 100 characters.")
    prefix = value.strip()
    if not prefix or len(prefix) > 100 or not prefix.isprintable():
        raise ValueError("Enter a clip title prefix of up to 100 printable characters.")
    return prefix


def validate_shared_metadata(shared: dict) -> dict:
    if not isinstance(shared, dict):
        raise ValueError("Enter the match details before uploading.")
    keys = ("eventName", "leftFencer", "rightFencer", "weapon", "sourceUrl")
    result = {key: str(shared.get(key, "")).strip() for key in keys}
    for key in keys:
        if not result[key]:
            raise ValueError(f"{key} is required.")
    source = urlparse(result["sourceUrl"])
    if source.scheme not in {"http", "https"} or not source.netloc:
        raise ValueError("Source URL must be an http(s) URL.")
    limits = {"eventName": 300, "leftFencer": 200, "rightFencer": 200,
              "weapon": 100, "sourceUrl": 2048}
    for key, limit in limits.items():
        if len(result[key]) > limit:
            raise ValueError(f"{key} is too long.")
    return result


def validate_review(job: dict) -> list[dict]:
    clips = job.get("clips", [])
    if not clips or job.get("state") != "ready":
        raise ValueError("Clips are not ready for upload.")
    if any(clip.get("decision") not in {"keep", "discard"} for clip in clips):
        raise ValueError("Review every clip before uploading.")
    kept = [clip for clip in clips if clip["decision"] == "keep"]
    if not kept:
        raise ValueError("No clips are marked usable.")
    for clip in kept:
        if not clip.get("title", "").strip() or not clip.get("scoreAtTouch", "").strip():
            raise ValueError(f"Clip {clip['index']:03d} needs a title and score at touch.")
        if len(clip["title"]) > 300 or len(clip["scoreAtTouch"]) > 100 or len(clip.get("notes", "")) > 5000:
            raise ValueError(f"Clip {clip['index']:03d} has metadata that is too long.")
    validate_shared_metadata(job.get("shared", {}))
    return kept


class ReviewManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.client: WhatsthecallClient | None = None
        for manifest in self.root.glob("*/manifest.json"):
            job = json.loads(manifest.read_text(encoding="utf-8"))
            changed = False
            if job["state"] == "processing":
                job.update(state="error", phase="Generation interrupted",
                           error="Generation stopped when the review app closed. Generate clips again.")
                changed = True
            if job["upload_state"] == "running":
                job.update(upload_state="partial", upload_error="Upload interrupted. Sign in and retry remaining clips.")
                for clip in job["clips"]:
                    if clip["upload_status"] in {"uploading", "registering"}:
                        clip.update(upload_status="failed", upload_error="Upload interrupted.")
                changed = True
            if changed:
                self._write(job)

    def _job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id):
            raise FileNotFoundError("Unknown job.")
        directory = self.root / job_id
        if not directory.is_dir():
            raise FileNotFoundError("Unknown job.")
        return directory

    def _read(self, job_id: str) -> dict:
        return json.loads((self._job_dir(job_id) / "manifest.json").read_text(encoding="utf-8"))

    def _write(self, job: dict) -> None:
        directory = self._job_dir(job["id"])
        job["updated_at"] = utc_now()
        temp = directory / "manifest.tmp"
        temp.write_text(json.dumps(job, indent=2), encoding="utf-8")
        os.replace(temp, directory / "manifest.json")

    def _remove_previous_trim(self, job_id: str, filename: str | None) -> None:
        if filename and re.fullmatch(r"phrase_\d{3,}_[0-9a-f]{12}\.mp4", filename):
            try:
                (self._job_dir(job_id) / "edited" / filename).unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, job_id: str) -> dict:
        with self.lock:
            return self._read(job_id)

    def latest(self) -> dict | None:
        with self.lock:
            jobs = sorted(self.root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            return json.loads(jobs[0].read_text(encoding="utf-8")) if jobs else None

    def _new_job(self, job_id: str, name: str, settings: dict, title_prefix: str = "Phrase") -> dict:
        return {
            "id": job_id, "source_name": Path(name).name, "state": "processing",
            "phase": "Analyzing video", "error": None, "created_at": utc_now(),
            "title_prefix": validate_title_prefix(title_prefix),
            "settings": {key: (asdict(value) if isinstance(value, Roi) else value) for key, value in settings.items()},
            "shared": {"eventName": "", "leftFencer": "", "rightFencer": "", "weapon": "sabre", "sourceUrl": ""},
            "clips": [], "upload_state": "idle", "upload_error": None, "upload_site": None,
        }

    def create_from_stream(self, name: str, size: int, stream, settings: dict, title_prefix: str = "Phrase") -> dict:
        title_prefix = validate_title_prefix(title_prefix)
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS or size < 1 or size > MAX_SOURCE_BYTES:
            raise ValueError("Choose an MP4, MOV, MKV, or WebM file up to 8 GB.")
        job_id = uuid.uuid4().hex[:12]
        directory = self.root / job_id
        directory.mkdir()
        source_path = directory / ("source" + suffix)
        with source_path.open("wb") as output:
            remaining = size
            while remaining:
                chunk = stream.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ValueError("The source video upload ended early.")
                output.write(chunk)
                remaining -= len(chunk)
        job = self._new_job(job_id, name, settings, title_prefix)
        with self.lock:
            self._write(job)
        threading.Thread(target=self._generate, args=(job_id,), daemon=True).start()
        return job

    def source_path(self, job_id: str) -> Path:
        job = self.get(job_id)
        suffix = Path(job["source_name"]).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise FileNotFoundError("Unknown source video.")
        return self._job_dir(job_id) / ("source" + suffix)

    def reanalyze(self, job_id: str, settings: dict, title_prefix: str | None = None) -> dict:
        original = self.get(job_id)
        if original["state"] == "processing":
            raise ValueError("Wait for the current analysis to finish.")
        title_prefix = validate_title_prefix(
            title_prefix if title_prefix is not None else original.get("title_prefix", "Phrase")
        )
        source = self.source_path(job_id)
        new_id = uuid.uuid4().hex[:12]
        directory = self.root / new_id
        directory.mkdir()
        try:
            os.link(source, directory / source.name)
        except OSError as exc:
            raise ValueError(f"Could not reuse the saved source video: {exc}") from exc
        job = self._new_job(new_id, original["source_name"], settings, title_prefix)
        with self.lock:
            self._write(job)
        threading.Thread(target=self._generate, args=(new_id,), daemon=True).start()
        return job

    def _generate(self, job_id: str) -> None:
        directory = self._job_dir(job_id)
        try:
            with self.lock:
                job = self._read(job_id)
            settings = job["settings"]
            source = self.source_path(job_id)
            duration = duration_seconds(source)
            if settings.get("reference_at") is not None and settings["reference_at"] > duration:
                raise ValueError("Known touch time is beyond the end of the recording.")
            samples = sample_video_lights(
                source, fps=settings["fps"], roi=Roi(**settings["roi"]),
                red_roi=Roi(**settings["red_roi"]) if settings.get("red_roi") else None,
                green_roi=Roi(**settings["green_roi"]) if settings.get("green_roi") else None,
                motion_roi=Roi(**settings["motion_roi"]),
                start_at=settings["start_at"], end_at=settings["end_at"],
            )
            samples = [s for s in samples if settings["start_at"] <= s.time <=
                       (settings["end_at"] if settings["end_at"] is not None else duration)]
            effective_pixels = settings["min_pixels"]
            calibration = None
            if settings.get("reference_at") is not None:
                effective_pixels, detected_at, color = calibrate_light_threshold(
                    samples, settings["reference_at"], minimum=effective_pixels,
                )
                calibration = {"detected_at": detected_at, "color": color, "min_pixels": effective_pixels}
            active_share = sum(
                max(sample.red_pixels, sample.green_pixels) >= effective_pixels for sample in samples
            ) / max(1, len(samples))
            if active_share > 0.4:
                raise ValueError(
                    "Colored graphics are active through too much of the recording. "
                    "Select tighter red/green light areas, mark a known touch, or adjust the bout range."
                )
            events = detect_light_onsets(
                samples, min_pixels=effective_pixels, min_gap=settings["min_gap"], colors={"red", "green"},
            )
            clips = build_video_phrase_clips(
                events, samples, lookback=settings["lookback"], media_duration=duration,
                start_mode="motion",
            )
            write_light_analysis(
                directory / "light_events.json", input_path=source, duration=duration,
                roi=Roi(**settings["roi"]), motion_roi=Roi(**settings["motion_roi"]),
                red_roi=Roi(**settings["red_roi"]) if settings.get("red_roi") else None,
                green_roi=Roi(**settings["green_roi"]) if settings.get("green_roi") else None,
                fps=settings["fps"], min_pixels=effective_pixels, start_mode="motion",
                colors={"red", "green"}, min_gap=settings["min_gap"],
                start_at=settings["start_at"], end_at=settings["end_at"],
                samples=samples, events=events, clips=clips,
            )
            analysis_path = directory / "light_events.json"
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            analysis["settings"].update({"event_mode": "onsets", "calibration": calibration})
            analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            with self.lock:
                job = self._read(job_id)
                job["settings"].update({"effective_min_pixels": effective_pixels, "calibration": calibration})
                job["phase"] = f"Cutting 0 of {len(clips)} clips"
                self._write(job)
            output_dir = directory / "clips"
            output_dir.mkdir(exist_ok=True)
            rows = []
            for number, clip in enumerate(clips, start=1):
                output = output_dir / f"phrase_{clip.index:03d}.mp4"
                cut_time_range(source, clip.clip_start, clip.clip_end, output, reencode=True)
                rows.append({
                    "index": clip.index, "file": output.name, "clip_start": clip.clip_start,
                    "clip_end": clip.clip_end, "event_time": clip.event_time,
                    "start_method": clip.start_method, "review_reasons": clip.review_reasons,
                    "decision": "pending", "title": f"{job.get('title_prefix', 'Phrase')} {clip.index:03d}",
                    "scoreAtTouch": "", "notes": "", "upload_status": "pending",
                    "s3_key": None, "remote_clip_id": None, "upload_error": None,
                })
                with self.lock:
                    job = self._read(job_id)
                    job["phase"] = f"Cutting {number} of {len(clips)} clips"
                    self._write(job)
            with self.lock:
                job = self._read(job_id)
                job["clips"] = rows
                job["state"] = "ready"
                job["phase"] = "Review clips"
                self._write(job)
        except Exception as exc:
            with self.lock:
                job = self._read(job_id)
                job["state"] = "error"
                job["error"] = str(exc)
                job["phase"] = "Generation failed"
                self._write(job)

    def review(self, job_id: str, payload: dict) -> dict:
        index = int(payload.get("index", 0))
        decision = payload.get("decision")
        if decision not in {"pending", "keep", "discard"}:
            raise ValueError("Choose usable, discard, or pending.")
        with self.lock:
            job = self._read(job_id)
            if job["state"] != "ready" or job["upload_state"] == "running":
                raise ValueError("Review is unavailable while processing or uploading.")
            clip = next((c for c in job["clips"] if c["index"] == index), None)
            if clip is None:
                raise ValueError("Unknown clip number.")
            if clip["upload_status"] == "uploaded":
                raise ValueError("An uploaded clip cannot be changed.")
            clip.update({
                "decision": decision,
                "title": str(payload.get("title", "")).strip()[:300],
                "scoreAtTouch": str(payload.get("scoreAtTouch", "")).strip()[:100],
                "notes": str(payload.get("notes", ""))[:5000],
            })
            self._write(job)
            return job

    def trim_clip(self, job_id: str, payload: dict) -> dict:
        index = int(payload.get("index", 0))
        try:
            begin = float(payload["begin"])
            end = float(payload["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Choose valid Begin and End times.") from exc
        if not math.isfinite(begin) or not math.isfinite(end):
            raise ValueError("Choose valid Begin and End times.")
        with self.lock:
            job = self._read(job_id)
            if job["state"] != "ready" or job["upload_state"] == "running":
                raise ValueError("Cropping is unavailable while processing or uploading.")
            clip = next((c for c in job["clips"] if c["index"] == index), None)
            if clip is None:
                raise ValueError("Unknown clip number.")
            if clip.get("s3_key") or clip["upload_status"] == "uploaded":
                raise ValueError("A clip already sent to S3 cannot be cropped.")
            original = self.clip_path(job_id, index)
            duration = duration_seconds(original)
            if begin < 0 or end > duration + 0.1 or round(end - begin, 3) < 0.1:
                raise ValueError("Keep at least 0.1 seconds inside the clip.")
            begin = round(begin, 3)
            end = round(min(end, duration), 3)
            if round(end - begin, 3) < 0.1:
                raise ValueError("Keep at least 0.1 seconds inside the clip.")
            if begin == clip.get("trim_begin", 0) and end == clip.get("trim_end", round(duration, 3)):
                return job
            previous = clip.get("trim_file")
            if begin <= 0.01 and end >= duration - 0.01:
                clip.update(trim_begin=0, trim_end=round(duration, 3), trim_file=None)
                self._write(job)
                self._remove_previous_trim(job_id, previous)
                return job
            edited_dir = self._job_dir(job_id) / "edited"
            edited_dir.mkdir(exist_ok=True)
            output = edited_dir / f"phrase_{index:03d}_{uuid.uuid4().hex[:12]}.mp4"
            try:
                cut_time_range(original, begin, end, output, reencode=True)
            except subprocess.CalledProcessError as exc:
                output.unlink(missing_ok=True)
                raise ValueError(f"Could not export crop: {exc.stderr[-500:] if exc.stderr else exc}") from exc
            clip.update(trim_begin=begin, trim_end=end, trim_file=output.name)
            self._write(job)
            self._remove_previous_trim(job_id, previous)
            return job

    def set_shared(self, job_id: str, shared: dict) -> dict:
        if not isinstance(shared, dict):
            raise ValueError("Invalid match details.")
        keys = ("eventName", "leftFencer", "rightFencer", "weapon", "sourceUrl")
        values = {key: str(shared.get(key, "")).strip() for key in keys}
        with self.lock:
            job = self._read(job_id)
            if job["upload_state"] == "running":
                raise ValueError("Match details cannot change during upload.")
            job["shared"] = values
            self._write(job)
            return job

    def login(self, site: str, email: str, password: str) -> dict:
        if not email or not password:
            raise ValueError("Enter your Whatsthecall email and password.")
        client = WhatsthecallClient(normalize_site_url(site))
        client.login(email, password)
        with self.lock:
            self.client = client
        return {"site": client.site_url, "signed_in": True}

    def start_upload(self, job_id: str) -> dict:
        with self.lock:
            job = self._read(job_id)
            kept = validate_review(job)
            if self.client is None:
                raise ValueError("Sign in to Whatsthecall before uploading.")
            if job["upload_state"] == "running":
                raise ValueError("Upload is already running.")
            if job["upload_site"] and job["upload_site"] != self.client.site_url:
                raise ValueError("This job was previously uploaded to a different Whatsthecall site.")
            if all(c["upload_status"] == "uploaded" for c in kept):
                raise ValueError("All usable clips have already been uploaded.")
            job["upload_site"] = self.client.site_url
            job["upload_state"] = "running"
            job["upload_error"] = None
            self._write(job)
        threading.Thread(target=self._upload, args=(job_id, self.client), daemon=True).start()
        return self.get(job_id)

    def _upload(self, job_id: str, client: WhatsthecallClient) -> None:
        with self.lock:
            job = self._read(job_id)
            shared = validate_shared_metadata(job["shared"])
            indices = [c["index"] for c in job["clips"] if c["decision"] == "keep" and c["upload_status"] != "uploaded"]
        for index in indices:
            with self.lock:
                job = self._read(job_id)
                clip = next(c for c in job["clips"] if c["index"] == index)
                clip["upload_status"] = "uploading"
                clip["upload_error"] = None
                self._write(job)
            try:
                trim_file = clip.get("trim_file")
                if trim_file and not re.fullmatch(rf"phrase_{index:03d}_[0-9a-f]{{12}}\.mp4", trim_file):
                    raise ValueError("Invalid cropped clip filename.")
                file = (self._job_dir(job_id) / "edited" / trim_file
                        if trim_file else self.clip_path(job_id, index))
                if not clip["s3_key"]:
                    if not file.is_file():
                        raise FileNotFoundError("Reviewed clip file is missing.")
                    url, key = client.presign(file)
                    client.put_video(url, file)
                    with self.lock:
                        job = self._read(job_id)
                        clip = next(c for c in job["clips"] if c["index"] == index)
                        clip["s3_key"] = key
                        clip["upload_status"] = "registering"
                        self._write(job)
                metadata = {
                    **shared, "title": clip["title"], "scoreAtTouch": clip["scoreAtTouch"],
                    "notes": clip["notes"],
                }
                clip_id = client.register(clip["s3_key"], metadata)
                with self.lock:
                    job = self._read(job_id)
                    clip = next(c for c in job["clips"] if c["index"] == index)
                    clip["upload_status"] = "uploaded"
                    clip["remote_clip_id"] = clip_id
                    self._write(job)
            except (UploadError, OSError, ValueError) as exc:
                with self.lock:
                    job = self._read(job_id)
                    clip = next(c for c in job["clips"] if c["index"] == index)
                    clip["upload_status"] = "failed"
                    clip["upload_error"] = str(exc)
                    self._write(job)
                if "HTTP 401" in str(exc):
                    break
        with self.lock:
            job = self._read(job_id)
            kept = [c for c in job["clips"] if c["decision"] == "keep"]
            job["upload_state"] = "done" if all(c["upload_status"] == "uploaded" for c in kept) else "partial"
            if any(c["upload_status"] == "failed" and "HTTP 401" in (c["upload_error"] or "") for c in kept):
                job["upload_error"] = "Session expired. Sign in again, then retry the remaining clips."
            self._write(job)

    def clip_path(self, job_id: str, index: int) -> Path:
        job = self.get(job_id)
        clip = next((c for c in job["clips"] if c["index"] == index), None)
        if clip is None or not re.fullmatch(r"phrase_\d{3,}\.mp4", clip["file"]):
            raise FileNotFoundError("Unknown clip.")
        return self._job_dir(job_id) / "clips" / clip["file"]


def make_handler(manager: ReviewManager):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BoutSplitterReview/0.1"

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            return data

        def _file(self, path: Path, mime: str) -> None:
            if not path.is_file():
                raise FileNotFoundError("File not found.")
            size = path.stat().st_size
            range_header = self.headers.get("Range", "")
            start, end = 0, size - 1
            if range_header:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
                if not match:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
                if start >= size or end < start:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return
                end = min(end, size - 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = handle.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_GET(self) -> None:
            try:
                url = urlparse(self.path)
                if url.path == "/":
                    return self._file(STATIC_DIR / "review.html", "text/html; charset=utf-8")
                if url.path == "/app.css":
                    return self._file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
                if url.path == "/app.js":
                    return self._file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
                if url.path == "/api/jobs/latest":
                    return self._json(200, {"job": manager.latest()})
                match = re.fullmatch(r"/source/([0-9a-f]{12})", url.path)
                if match:
                    source = manager.source_path(match.group(1))
                    return self._file(source, "video/mp4")
                match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", url.path)
                if match:
                    return self._json(200, {"job": manager.get(match.group(1))})
                match = re.fullmatch(r"/media/([0-9a-f]{12})/(\d{1,4})", url.path)
                if match:
                    return self._file(manager.clip_path(match.group(1), int(match.group(2))), "video/mp4")
                self.send_error(404)
            except (FileNotFoundError, ValueError):
                self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self) -> None:
            if self.headers.get("X-Local-Request") != "review-ui":
                return self._json(403, {"error": "Local request header required."})
            try:
                url = urlparse(self.path)
                if url.path == "/api/jobs":
                    size = int(self.headers.get("Content-Length", "0"))
                    query = parse_qs(url.query, keep_blank_values=True)
                    name = query.get("name", [""])[0]
                    settings = validate_settings(json.loads(query.get("settings", ["{}"])[0]))
                    title_prefix = validate_title_prefix(query.get("titlePrefix", ["Phrase"])[0])
                    return self._json(201, {"job": manager.create_from_stream(
                        name, size, self.rfile, settings, title_prefix,
                    )})
                if url.path == "/api/login":
                    data = self._read_json()
                    return self._json(200, manager.login(data.get("site", ""), data.get("email", ""), data.get("password", "")))
                match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})/(review|trim|shared|upload)", url.path)
                if match:
                    job_id, action = match.groups()
                    if action == "review":
                        return self._json(200, {"job": manager.review(job_id, self._read_json())})
                    if action == "trim":
                        return self._json(200, {"job": manager.trim_clip(job_id, self._read_json())})
                    if action == "shared":
                        return self._json(200, {"job": manager.set_shared(job_id, self._read_json())})
                    if action == "upload":
                        self._read_json()
                        return self._json(202, {"job": manager.start_upload(job_id)})
                match = re.fullmatch(r"/api/jobs/([0-9a-f]{12})/reanalyze", url.path)
                if match:
                    data = self._read_json()
                    return self._json(201, {"job": manager.reanalyze(
                        match.group(1), validate_settings(data), data.get("titlePrefix"),
                    )})
                self._json(404, {"error": "Unknown endpoint."})
            except FileNotFoundError:
                self._json(404, {"error": "Unknown job or clip."})
            except (ValueError, TypeError, json.JSONDecodeError, UploadError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": f"Request failed: {exc}"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review fencing clips locally and upload approved clips.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs", type=Path, default=Path.cwd() / "runs")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    manager = ReviewManager(args.runs)
    try:
        server = LocalReviewServer(("127.0.0.1", args.port), make_handler(manager))
    except OSError as exc:
        raise SystemExit(f"Could not start review app on port {args.port}. Is it already running? {exc}") from exc
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Bout Splitter review UI: {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
