from __future__ import annotations

import json
import math
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .media import find_media_tool
from .models import LightEvent, LightSample, VideoPhraseClip, to_plain_json


class VideoAnalysisUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Roi:
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 1.0
    y2: float = 0.72


def parse_roi(value: str | None) -> Roi:
    if not value:
        return Roi()
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("ROI must have four comma-separated numbers: x1,y1,x2,y2")
    roi = Roi(*parts)
    if not (0 <= roi.x1 < roi.x2 <= 1 and 0 <= roi.y1 < roi.y2 <= 1):
        raise ValueError("ROI values must be normalized fractions with x1<x2 and y1<y2.")
    return roi


def _require_video_dependencies():
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise VideoAnalysisUnavailable(
            "Video analysis requires PyAV and NumPy. Install with: python -m pip install -e \".[video]\""
        ) from exc
    return av, np


def _crop(frame, roi: Roi):
    height, width = frame.shape[:2]
    x1 = int(width * roi.x1)
    y1 = int(height * roi.y1)
    x2 = max(x1 + 1, int(width * roi.x2))
    y2 = max(y1 + 1, int(height * roi.y2))
    return frame[y1:y2, x1:x2]


def score_frame(frame, *, roi: Roi = Roi(), previous_frame=None, motion_roi: Roi | None = None) -> LightSample:
    _av, np = _require_video_dependencies()
    region = _crop(frame, roi).astype(np.int16)
    red = region[:, :, 0]
    green = region[:, :, 1]
    blue = region[:, :, 2]
    area = max(1, region.shape[0] * region.shape[1])

    red_mask = (red >= 185) & (red > green * 1.45) & (red > blue * 1.45)
    green_mask = (green >= 165) & (green > red * 1.25) & (green > blue * 1.25)
    white_mask = (red >= 220) & (green >= 220) & (blue >= 220) & ((region.max(axis=2) - region.min(axis=2)) <= 35)

    motion = 0.0
    if previous_frame is not None:
        motion_region = motion_roi or roi
        current_gray = _crop(frame, motion_region).astype(np.int16).mean(axis=2)
        previous_gray = _crop(previous_frame, motion_region).astype(np.int16).mean(axis=2)
        motion = float(np.mean(np.abs(current_gray - previous_gray)) / 255.0)

    red_pixels = int(red_mask.sum())
    green_pixels = int(green_mask.sum())
    white_pixels = int(white_mask.sum())
    return LightSample(
        time=0.0,
        red_pixels=red_pixels,
        green_pixels=green_pixels,
        white_pixels=white_pixels,
        red_ratio=red_pixels / area,
        green_ratio=green_pixels / area,
        white_ratio=white_pixels / area,
        motion=motion,
    )


def sample_video_lights(
    input_path: str | Path,
    *,
    fps: float = 5.0,
    roi: Roi = Roi(),
    motion_roi: Roi | None = None,
) -> list[LightSample]:
    av, _np = _require_video_dependencies()
    samples: list[LightSample] = []
    interval = 1.0 / fps
    next_time = 0.0
    previous_frame = None

    with av.open(str(input_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            time = float(frame.time)
            if time + 1e-6 < next_time:
                continue
            rgb = frame.to_ndarray(format="rgb24")
            score = score_frame(rgb, roi=roi, previous_frame=previous_frame, motion_roi=motion_roi)
            samples.append(
                LightSample(
                    time=round(time, 3),
                    red_pixels=score.red_pixels,
                    green_pixels=score.green_pixels,
                    white_pixels=score.white_pixels,
                    red_ratio=score.red_ratio,
                    green_ratio=score.green_ratio,
                    white_ratio=score.white_ratio,
                    motion=score.motion,
                )
            )
            previous_frame = rgb
            while next_time <= time + 1e-6:
                next_time += interval
    return samples


def _dominant_color(sample: LightSample, colors: set[str] | None = None) -> tuple[str, int]:
    values = {
        "red": sample.red_pixels,
        "green": sample.green_pixels,
        "white": sample.white_pixels,
    }
    if colors is not None:
        values = {key: value for key, value in values.items() if key in colors}
    return max(values.items(), key=lambda item: item[1])


def detect_light_events(
    samples: list[LightSample],
    *,
    min_pixels: int = 80,
    min_gap: float = 1.5,
    min_duration: float = 0.0,
    colors: set[str] | None = None,
) -> list[LightEvent]:
    events: list[LightEvent] = []
    current: list[LightSample] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        start = current[0].time
        end = current[-1].time
        if end - start < min_duration:
            current = []
            return
        peak = max(current, key=lambda sample: _dominant_color(sample, colors)[1])
        color, pixels = _dominant_color(peak, colors)
        confidence = min(1.0, pixels / max(min_pixels * 4, 1))
        events.append(
            LightEvent(
                index=len(events) + 1,
                start=round(start, 3),
                end=round(end, 3),
                peak_time=round(peak.time, 3),
                color=color,
                red_pixels=peak.red_pixels,
                green_pixels=peak.green_pixels,
                white_pixels=peak.white_pixels,
                confidence=round(confidence, 3),
            )
        )
        current = []

    for sample in samples:
        _color, selected_pixels = _dominant_color(sample, colors)
        active = selected_pixels >= min_pixels
        if active:
            if current and sample.time - current[-1].time > min_gap:
                flush()
            current.append(sample)
        elif current and sample.time - current[-1].time > min_gap:
            flush()
    flush()
    return events


def estimate_motion_threshold(samples: list[LightSample]) -> float:
    values = [sample.motion for sample in samples if sample.motion > 0]
    if not values:
        return 0.015
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations) if deviations else 0.0
    return max(0.012, median + (mad * 1.5))


def build_video_phrase_clips(
    events: list[LightEvent],
    samples: list[LightSample],
    *,
    lookback: float = 8.0,
    pre_roll: float = 0.5,
    post_roll: float = 2.0,
    media_duration: float | None = None,
    motion_threshold: float | None = None,
    start_mode: str = "motion",
) -> list[VideoPhraseClip]:
    threshold = motion_threshold if motion_threshold is not None else estimate_motion_threshold(samples)
    clips: list[VideoPhraseClip] = []

    for event in events:
        floor = max(0.0, event.start - lookback)
        start = floor
        if start_mode == "motion":
            window = [sample for sample in samples if floor <= sample.time <= event.start]
            for sample in reversed(window):
                if sample.motion <= threshold:
                    start = max(0.0, sample.time - pre_roll)
                    break
        elif start_mode != "fixed-lookback":
            raise ValueError("start_mode must be 'motion' or 'fixed-lookback'.")
        end = event.peak_time + post_roll
        if media_duration is not None:
            end = min(end, media_duration)
        clips.append(
            VideoPhraseClip(
                index=len(clips) + 1,
                clip_start=round(start, 3),
                clip_end=round(max(start + 0.01, end), 3),
                event_time=event.peak_time,
                event_color=event.color,
                event=event,
                confidence=event.confidence,
            )
        )
    return clips


def write_light_analysis(
    output_path: str | Path,
    *,
    input_path: str | Path,
    duration: float,
    roi: Roi,
    motion_roi: Roi | None,
    fps: float,
    min_pixels: int,
    start_mode: str,
    colors: set[str],
    min_gap: float,
    start_at: float | None,
    end_at: float | None,
    samples: list[LightSample],
    events: list[LightEvent],
    clips: list[VideoPhraseClip],
) -> None:
    payload = {
        "input": str(input_path),
        "duration": duration,
        "settings": {
            "fps": fps,
            "roi": to_plain_json(roi),
            "motion_roi": to_plain_json(motion_roi),
            "min_pixels": min_pixels,
            "colors": sorted(colors),
            "min_gap": min_gap,
            "start_at": start_at,
            "end_at": end_at,
            "start_mode": start_mode,
        },
        "events": to_plain_json(events),
        "clips": to_plain_json(clips),
        "samples": to_plain_json(samples),
    }
    Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_light_debug_sheet(
    input_path: str | Path,
    output_path: str | Path,
    events: list[LightEvent],
    *,
    ffmpeg_path: str | None = None,
    max_events: int = 24,
) -> None:
    if not events:
        return
    ffmpeg = ffmpeg_path or find_media_tool("ffmpeg")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    width = 320
    frames = min(len(events), max_events)
    rows = math.ceil(frames / 4)
    timestamps = "|".join(f"eq(n\\,{index})" for index in range(frames))
    # Generate a temporary CFR stream of event frames, then tile the first N frames.
    select_parts = []
    for event in events[:max_events]:
        select_parts.append(f"between(t\\,{max(0.0, event.peak_time - 0.04):.3f}\\,{event.peak_time + 0.04:.3f})")
    select_expr = "+".join(select_parts)
    vf = f"select='{select_expr}',scale={width}:-1,setpts=N/FRAME_RATE/TB,tile=4x{rows}"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
