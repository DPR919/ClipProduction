from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from .media import find_media_tool
from .models import PhraseClip, VideoPhraseClip


def cut_time_range(
    input_path: str | Path,
    start: float,
    end: float,
    output_path: str | Path,
    *,
    ffmpeg_path: str | None = None,
    reencode: bool = False,
) -> None:
    ffmpeg = ffmpeg_path or find_media_tool("ffmpeg")
    duration = max(0.01, end - start)
    args = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration:.3f}",
    ]
    if reencode:
        args.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"])
    else:
        args.extend(["-c", "copy"])
    args.append(str(output_path))
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def cut_phrase_clip(
    input_path: str | Path,
    clip: PhraseClip,
    output_path: str | Path,
    *,
    ffmpeg_path: str | None = None,
    reencode: bool = False,
) -> None:
    cut_time_range(
        input_path,
        clip.clip_start,
        clip.clip_end,
        output_path,
        ffmpeg_path=ffmpeg_path,
        reencode=reencode,
    )


def cut_phrase_clips(
    input_path: str | Path,
    clips: list[PhraseClip],
    output_dir: str | Path,
    *,
    ffmpeg_path: str | None = None,
    reencode: bool = False,
) -> list[PhraseClip]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    completed: list[PhraseClip] = []
    for clip in clips:
        output = out_dir / f"phrase_{clip.index:03d}.mp4"
        cut_phrase_clip(
            input_path,
            clip,
            output,
            ffmpeg_path=ffmpeg_path,
            reencode=reencode,
        )
        completed.append(
            PhraseClip(
                index=clip.index,
                clip_start=clip.clip_start,
                clip_end=clip.clip_end,
                en_garde=clip.en_garde,
                halt=clip.halt,
                action_start=clip.action_start,
                signals=clip.signals,
                output=str(output),
                confidence=clip.confidence,
            )
        )
    return completed


def cut_video_phrase_clips(
    input_path: str | Path,
    clips: list[VideoPhraseClip],
    output_dir: str | Path,
    *,
    ffmpeg_path: str | None = None,
    reencode: bool = True,
) -> list[VideoPhraseClip]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    completed: list[VideoPhraseClip] = []
    for clip in clips:
        output = out_dir / f"light_phrase_{clip.index:03d}.mp4"
        cut_time_range(
            input_path,
            clip.clip_start,
            clip.clip_end,
            output,
            ffmpeg_path=ffmpeg_path,
            reencode=reencode,
        )
        completed.append(
            replace(clip, output=str(output))
        )
    return completed
