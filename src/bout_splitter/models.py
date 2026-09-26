from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptWord:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True)
class Signal:
    kind: str
    time: float
    end: float | None = None
    text: str = ""
    confidence: float = 1.0
    source: str = "audio"


@dataclass(frozen=True)
class PhraseClip:
    index: int
    clip_start: float
    clip_end: float
    en_garde: float
    halt: float
    action_start: float | None = None
    signals: list[Signal] = field(default_factory=list)
    output: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class LightSample:
    time: float
    red_pixels: int
    green_pixels: int
    white_pixels: int
    red_ratio: float
    green_ratio: float
    white_ratio: float
    motion: float


@dataclass(frozen=True)
class LightEvent:
    index: int
    start: float
    end: float
    peak_time: float
    color: str
    red_pixels: int
    green_pixels: int
    white_pixels: int
    confidence: float


@dataclass(frozen=True)
class VideoPhraseClip:
    index: int
    clip_start: float
    clip_end: float
    event_time: float
    event_color: str
    event: LightEvent
    output: str | None = None
    confidence: float = 1.0
    start_method: str = "unknown"
    review_reasons: list[str] = field(default_factory=list)


def to_plain_json(value: Any) -> Any:
    if isinstance(value, tuple):
        return [to_plain_json(item) for item in value]
    if isinstance(value, list):
        return [to_plain_json(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_plain_json(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain_json(item) for key, item in value.items()}
    return value
