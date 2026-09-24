from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import TranscriptSegment, TranscriptWord, to_plain_json


class TranscriptionUnavailable(RuntimeError):
    pass


def _word_from_mapping(item: dict[str, Any]) -> TranscriptWord:
    return TranscriptWord(
        start=float(item["start"]),
        end=float(item.get("end", item["start"])),
        text=str(item.get("word", item.get("text", ""))).strip(),
    )


def _segment_from_mapping(item: dict[str, Any]) -> TranscriptSegment:
    words = tuple(_word_from_mapping(word) for word in item.get("words", []) if "start" in word)
    return TranscriptSegment(
        start=float(item["start"]),
        end=float(item.get("end", item["start"])),
        text=str(item.get("text", "")).strip(),
        words=words,
    )


def load_transcript(path: str | Path) -> list[TranscriptSegment]:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        if "segments" in data:
            data = data["segments"]
        else:
            raise ValueError("Transcript JSON object must contain a 'segments' key.")

    if not isinstance(data, list):
        raise ValueError("Transcript JSON must be a list of segments or an object with 'segments'.")

    return [_segment_from_mapping(item) for item in data]


def save_transcript(path: str | Path, segments: list[TranscriptSegment]) -> None:
    payload = {"segments": to_plain_json(segments)}
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def transcribe_audio(
    audio_path: str | Path,
    model_name: str = "small",
    language: str | None = "en",
) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "faster-whisper is not installed. Install the 'transcribe' extra or pass --transcript."
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    raw_segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        word_timestamps=True,
    )

    segments: list[TranscriptSegment] = []
    for raw in raw_segments:
        words = tuple(
            TranscriptWord(start=float(word.start), end=float(word.end), text=str(word.word).strip())
            for word in (raw.words or [])
            if word.start is not None
        )
        segments.append(
            TranscriptSegment(
                start=float(raw.start),
                end=float(raw.end),
                text=str(raw.text).strip(),
                words=words,
            )
        )
    return segments
