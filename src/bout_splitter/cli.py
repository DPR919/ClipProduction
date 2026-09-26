from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .cutter import cut_phrase_clips, cut_video_phrase_clips
from .media import duration_seconds, extract_audio_wav, find_media_tool, probe_video
from .models import LightEvent, LightSample, to_plain_json
from .segmenter import pair_phrases
from .signals import detect_audio_signals
from .transcript import load_transcript, save_transcript, transcribe_audio
from .video import (
    build_video_phrase_clips,
    detect_light_events,
    parse_roi,
    sample_video_lights,
    write_light_analysis,
    write_light_debug_sheet,
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def command_probe(args: argparse.Namespace) -> int:
    data = probe_video(args.input, ffprobe_path=args.ffprobe)
    _print_json(data)
    return 0


def _load_or_create_transcript(args: argparse.Namespace, out_dir: Path):
    if args.transcript:
        return load_transcript(args.transcript)

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bout_splitter_") as temp_dir:
        audio_path = Path(temp_dir) / "audio.wav"
        extract_audio_wav(args.input, audio_path, ffmpeg_path=args.ffmpeg)
        segments = transcribe_audio(
            audio_path,
            model_name=args.whisper_model,
            language=args.language,
        )
    save_transcript(out_dir / "transcript.json", segments)
    return segments


def command_detect(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    media_duration = duration_seconds(args.input, ffprobe_path=args.ffprobe)
    transcript = _load_or_create_transcript(args, out_dir)
    signals = detect_audio_signals(transcript)
    clips = pair_phrases(
        signals,
        pre_roll=args.pre_roll,
        post_roll=args.post_roll,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        media_duration=media_duration,
    )
    _print_json(
        {
            "input": str(args.input),
            "duration": media_duration,
            "signals": to_plain_json(signals),
            "phrases": to_plain_json(clips),
        }
    )
    return 0


def command_split(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    media_duration = duration_seconds(args.input, ffprobe_path=args.ffprobe)
    transcript = _load_or_create_transcript(args, out_dir)
    signals = detect_audio_signals(transcript)
    clips = pair_phrases(
        signals,
        pre_roll=args.pre_roll,
        post_roll=args.post_roll,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        media_duration=media_duration,
    )

    if args.dry_run:
        completed = clips
    else:
        completed = cut_phrase_clips(
            args.input,
            clips,
            out_dir,
            ffmpeg_path=args.ffmpeg,
            reencode=args.reencode,
        )

    payload = {
        "input": str(args.input),
        "duration": media_duration,
        "settings": {
            "pre_roll": args.pre_roll,
            "post_roll": args.post_roll,
            "min_duration": args.min_duration,
            "max_duration": args.max_duration,
            "dry_run": args.dry_run,
            "reencode": args.reencode,
        },
        "signals": to_plain_json(signals),
        "phrases": to_plain_json(completed),
    }
    (out_dir / "detections.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print_json({"phrases": len(completed), "detections": str(out_dir / "detections.json")})
    return 0


def _analyze_lights(args: argparse.Namespace):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    roi = parse_roi(args.roi)
    motion_roi = parse_roi(args.motion_roi) if args.motion_roi else None
    media_duration = duration_seconds(args.input, ffprobe_path=args.ffprobe)
    samples = sample_video_lights(args.input, fps=args.fps, roi=roi, motion_roi=motion_roi)
    if args.start_at is not None or args.end_at is not None:
        start_at = args.start_at if args.start_at is not None else 0.0
        end_at = args.end_at if args.end_at is not None else media_duration
        samples = [sample for sample in samples if start_at <= sample.time <= end_at]
    colors = {color.strip() for color in args.colors.split(",") if color.strip()}
    events = detect_light_events(
        samples,
        min_pixels=args.min_pixels,
        min_gap=args.min_gap,
        min_duration=args.min_event_duration,
        colors=colors,
    )
    clips = build_video_phrase_clips(
        events,
        samples,
        lookback=args.lookback,
        pre_roll=args.pre_roll,
        post_roll=args.post_roll,
        media_duration=media_duration,
        motion_threshold=args.motion_threshold,
        start_mode=args.start_mode,
        quiet_duration=args.quiet_duration,
    )
    analysis_path = out_dir / "light_events.json"
    write_light_analysis(
        analysis_path,
        input_path=args.input,
        duration=media_duration,
        roi=roi,
        motion_roi=motion_roi,
        fps=args.fps,
        min_pixels=args.min_pixels,
        start_mode=args.start_mode,
        colors=colors,
        min_gap=args.min_gap,
        start_at=args.start_at,
        end_at=args.end_at,
        samples=samples,
        events=events,
        clips=clips,
    )
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    payload["settings"].update({
        "lookback": args.lookback, "pre_roll": args.pre_roll,
        "post_roll": args.post_roll, "quiet_duration": args.quiet_duration,
        "motion_threshold": args.motion_threshold,
        "min_event_duration": args.min_event_duration,
    })
    analysis_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.debug_sheet:
        write_light_debug_sheet(
            args.input,
            out_dir / "light_debug_contact_sheet.jpg",
            events,
            ffmpeg_path=args.ffmpeg,
            max_events=args.max_debug_events,
        )
    return analysis_path, events, clips


def command_analyze_lights(args: argparse.Namespace) -> int:
    analysis_path, events, clips = _analyze_lights(args)
    _print_json(
        {
            "events": len(events),
            "clips": len(clips),
            "analysis": str(analysis_path),
        }
    )
    return 0


def command_split_lights(args: argparse.Namespace) -> int:
    analysis_path, events, clips = _analyze_lights(args)
    if args.dry_run:
        completed = clips
    else:
        completed = cut_video_phrase_clips(
            args.input,
            clips,
            args.out,
            ffmpeg_path=args.ffmpeg,
            reencode=args.reencode,
        )
        analysis_payload = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
        analysis_payload["clips"] = to_plain_json(completed)
        Path(analysis_path).write_text(json.dumps(analysis_payload, indent=2), encoding="utf-8")

    _print_json(
        {
            "events": len(events),
            "clips": len(completed),
            "analysis": str(analysis_path),
        }
    )
    return 0


def command_tools(_args: argparse.Namespace) -> int:
    _print_json({"ffmpeg": find_media_tool("ffmpeg"), "ffprobe": find_media_tool("ffprobe")})
    return 0


def command_refine_lights(args: argparse.Namespace) -> int:
    source = json.loads(args.analysis.read_text(encoding="utf-8"))
    out_dir = args.out.resolve()
    if out_dir == args.analysis.resolve().parent or list(out_dir.glob("light_phrase_*.mp4")):
        raise ValueError("Choose a fresh output directory to preserve earlier clips and numbering.")
    events = [LightEvent(**item) for item in source["events"]]
    samples = [LightSample(**item) for item in source["samples"]]
    clips = build_video_phrase_clips(
        events, samples, lookback=args.lookback, pre_roll=args.pre_roll,
        post_roll=args.post_roll, media_duration=source["duration"],
        quiet_duration=args.quiet_duration, motion_threshold=args.motion_threshold,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.dry_run:
        clips = cut_video_phrase_clips(source["input"], clips, out_dir, ffmpeg_path=args.ffmpeg)
    old_clips = {c["event"]["index"]: c for c in source["clips"]}
    comparisons = []
    for clip in clips:
        old = old_clips.get(clip.event.index)
        comparisons.append({
            "original_clip": old["index"] if old else None,
            "revised_clip": clip.index,
            "old_start": old["clip_start"] if old else None,
            "new_start": clip.clip_start,
            "old_end": old["clip_end"] if old else None,
            "new_end": clip.clip_end,
            "start_method": clip.start_method,
        })
    source["refined_from"] = str(args.analysis.resolve())
    source["settings"].update({
        "lookback": args.lookback, "pre_roll": args.pre_roll,
        "post_roll": args.post_roll, "quiet_duration": args.quiet_duration,
        "motion_threshold": args.motion_threshold, "start_mode": "motion",
        "reencode": True, "dry_run": args.dry_run,
    })
    source["clips"] = to_plain_json(clips)
    source["comparisons"] = comparisons
    (out_dir / "light_events.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    _print_json({"clips": len(clips), "analysis": str(out_dir / "light_events.json"), "dry_run": args.dry_run})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bout-splitter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools = subparsers.add_parser("tools", help="Show resolved FFmpeg tools.")
    tools.set_defaults(func=command_tools)

    probe = subparsers.add_parser("probe", help="Print ffprobe JSON for a video.")
    probe.add_argument("input", type=Path)
    probe.add_argument("--ffprobe")
    probe.set_defaults(func=command_probe)

    def add_detection_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("input", type=Path)
        command.add_argument("--out", type=Path, default=Path("clips"))
        command.add_argument("--transcript", type=Path)
        command.add_argument("--whisper-model", default="small")
        command.add_argument("--language", default="en")
        command.add_argument("--pre-roll", type=float, default=0.5)
        command.add_argument("--post-roll", type=float, default=1.5)
        command.add_argument("--min-duration", type=float, default=1.0)
        command.add_argument("--max-duration", type=float, default=45.0)
        command.add_argument("--ffmpeg")
        command.add_argument("--ffprobe")

    detect = subparsers.add_parser("detect", help="Detect phrase boundaries without cutting clips.")
    add_detection_args(detect)
    detect.set_defaults(func=command_detect)

    split = subparsers.add_parser("split", help="Detect phrases and cut clips.")
    add_detection_args(split)
    split.add_argument("--dry-run", action="store_true")
    split.add_argument("--reencode", action="store_true", help="Re-encode clips for frame-accurate starts.")
    split.set_defaults(func=command_split)

    def add_light_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("input", type=Path)
        command.add_argument("--out", type=Path, default=Path("light_analysis"))
        command.add_argument("--fps", type=float, default=5.0)
        command.add_argument(
            "--roi",
            default="0,0,1,0.72",
            help="Normalized analysis region x1,y1,x2,y2. Default excludes lower broadcast overlay.",
        )
        command.add_argument(
            "--motion-roi",
            default="0,0,1,0.78",
            help="Normalized region for motion-based start detection.",
        )
        command.add_argument("--min-pixels", type=int, default=80)
        command.add_argument("--min-gap", type=float, default=1.5)
        command.add_argument("--min-event-duration", type=float, default=0.0)
        command.add_argument("--start-at", type=float, help="Ignore samples before this timestamp in seconds.")
        command.add_argument("--end-at", type=float, help="Ignore samples after this timestamp in seconds.")
        command.add_argument("--colors", default="red,green", help="Comma-separated colors to detect: red,green,white.")
        command.add_argument("--lookback", type=float, default=8.0)
        command.add_argument("--pre-roll", type=float, default=0.5)
        command.add_argument("--post-roll", type=float, default=2.0)
        command.add_argument("--motion-threshold", type=float)
        command.add_argument("--quiet-duration", type=float, default=0.6)
        command.add_argument("--start-mode", choices=["motion", "fixed-lookback"], default="motion")
        command.add_argument("--debug-sheet", action=argparse.BooleanOptionalAction, default=True)
        command.add_argument("--max-debug-events", type=int, default=24)
        command.add_argument("--ffmpeg")
        command.add_argument("--ffprobe")

    lights = subparsers.add_parser("analyze-lights", help="Detect scoring-light-like video events.")
    add_light_args(lights)
    lights.set_defaults(func=command_analyze_lights)

    split_lights = subparsers.add_parser("split-lights", help="Cut clips from detected light events.")
    add_light_args(split_lights)
    split_lights.add_argument("--dry-run", action="store_true")
    encoding = split_lights.add_mutually_exclusive_group()
    encoding.add_argument("--reencode", dest="reencode", action="store_true", default=True, help="Frame-accurate cutting (default).")
    encoding.add_argument("--stream-copy", dest="reencode", action="store_false", help="Fast but may retain video before the requested start.")
    split_lights.set_defaults(func=command_split_lights)

    refine = subparsers.add_parser("refine-lights", help="Revise starts from a saved analysis, keeping its light events.")
    refine.add_argument("analysis", type=Path)
    refine.add_argument("--out", type=Path, required=True)
    refine.add_argument("--lookback", type=float, default=8.0)
    refine.add_argument("--pre-roll", type=float, default=0.5)
    refine.add_argument("--post-roll", type=float, default=2.0)
    refine.add_argument("--quiet-duration", type=float, default=0.6)
    refine.add_argument("--motion-threshold", type=float)
    refine.add_argument("--ffmpeg")
    refine.add_argument("--dry-run", action="store_true")
    refine.set_defaults(func=command_refine_lights)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
