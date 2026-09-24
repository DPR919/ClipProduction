from __future__ import annotations

import re
import unicodedata

from .models import Signal, TranscriptSegment, TranscriptWord


SIGNAL_PATTERNS = {
    "en_garde": [
        re.compile(r"\ben\s+garde\b"),
        re.compile(r"\bengarde\b"),
        re.compile(r"\bon\s+guard\b"),
    ],
    "allez": [
        re.compile(r"\ballez\b"),
        re.compile(r"\ballay\b"),
    ],
    "halt": [
        re.compile(r"\bhalt\b"),
        re.compile(r"\bhalte\b"),
        re.compile(r"\bhal\b"),
    ],
}


def normalize_text(text: str) -> str:
    without_accents = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in without_accents if not unicodedata.combining(ch))
    ascii_text = ascii_text.lower().replace("-", " ")
    ascii_text = re.sub(r"[^a-z0-9\s]", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip()


def _word_signal(words: tuple[TranscriptWord, ...], kind: str) -> Signal | None:
    normalized_words = [normalize_text(word.text) for word in words]
    if kind == "en_garde":
        for i in range(len(normalized_words) - 1):
            if normalized_words[i] in {"en", "on"} and normalized_words[i + 1] in {"garde", "guard"}:
                return Signal(kind=kind, time=words[i].start, end=words[i + 1].end, text=" ".join(w.text for w in words[i : i + 2]))
        for i, token in enumerate(normalized_words):
            if token == "engarde":
                return Signal(kind=kind, time=words[i].start, end=words[i].end, text=words[i].text)

    if kind == "allez":
        for i, token in enumerate(normalized_words):
            if token in {"allez", "allay"}:
                return Signal(kind=kind, time=words[i].start, end=words[i].end, text=words[i].text)

    if kind == "halt":
        for i, token in enumerate(normalized_words):
            if token in {"halt", "halte", "hal"}:
                return Signal(kind=kind, time=words[i].start, end=words[i].end, text=words[i].text)

    return None


def detect_audio_signals(segments: list[TranscriptSegment]) -> list[Signal]:
    signals: list[Signal] = []
    for segment in segments:
        found_kinds: set[str] = set()
        if segment.words:
            for kind in SIGNAL_PATTERNS:
                signal = _word_signal(segment.words, kind)
                if signal:
                    signals.append(signal)
                    found_kinds.add(kind)

        normalized = normalize_text(segment.text)
        for kind, patterns in SIGNAL_PATTERNS.items():
            if kind in found_kinds:
                continue
            if any(pattern.search(normalized) for pattern in patterns):
                signals.append(
                    Signal(
                        kind=kind,
                        time=segment.start,
                        end=segment.end,
                        text=segment.text,
                        confidence=0.75,
                    )
                )

    return sorted(signals, key=lambda signal: (signal.time, signal.kind))

