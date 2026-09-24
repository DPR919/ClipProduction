from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class MediaToolError(RuntimeError):
    pass


def find_media_tool(name: str) -> str:
    env_name = f"{name.upper()}_PATH"
    if os.environ.get(env_name):
        path = Path(os.environ[env_name])
        if path.exists():
            return str(path)

    found = shutil.which(name)
    if found:
        return found

    exe = name if name.endswith(".exe") else f"{name}.exe"
    winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        matches = sorted(winget_root.glob(f"**/{exe}"), key=lambda item: str(item).lower())
        if matches:
            return str(matches[-1])

    raise MediaToolError(
        f"Could not find {exe}. Install FFmpeg or set {env_name} to the full executable path."
    )


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def probe_video(input_path: str | Path, ffprobe_path: str | None = None) -> dict[str, Any]:
    ffprobe = ffprobe_path or find_media_tool("ffprobe")
    completed = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ]
    )
    return json.loads(completed.stdout)


def duration_seconds(input_path: str | Path, ffprobe_path: str | None = None) -> float:
    data = probe_video(input_path, ffprobe_path=ffprobe_path)
    return float(data.get("format", {}).get("duration", 0.0))


def extract_audio_wav(
    input_path: str | Path,
    output_path: str | Path,
    ffmpeg_path: str | None = None,
) -> None:
    ffmpeg = ffmpeg_path or find_media_tool("ffmpeg")
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )

