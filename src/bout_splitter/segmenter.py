from __future__ import annotations

from .models import PhraseClip, Signal


def pair_phrases(
    signals: list[Signal],
    *,
    pre_roll: float = 0.5,
    post_roll: float = 1.5,
    min_duration: float = 1.0,
    max_duration: float = 45.0,
    media_duration: float | None = None,
) -> list[PhraseClip]:
    ordered = sorted(signals, key=lambda signal: signal.time)
    clips: list[PhraseClip] = []
    pending_start: Signal | None = None
    action_start: Signal | None = None
    phrase_signals: list[Signal] = []

    for signal in ordered:
        if signal.kind == "en_garde":
            pending_start = signal
            action_start = None
            phrase_signals = [signal]
            continue

        if pending_start is None:
            continue

        phrase_signals.append(signal)
        if signal.kind == "allez" and action_start is None:
            action_start = signal
            continue

        if signal.kind != "halt":
            continue

        duration = signal.time - pending_start.time
        if min_duration <= duration <= max_duration:
            clip_start = max(0.0, pending_start.time - pre_roll)
            clip_end = signal.time + post_roll
            if media_duration is not None:
                clip_end = min(media_duration, clip_end)
            clips.append(
                PhraseClip(
                    index=len(clips) + 1,
                    clip_start=round(clip_start, 3),
                    clip_end=round(max(clip_start, clip_end), 3),
                    en_garde=round(pending_start.time, 3),
                    halt=round(signal.time, 3),
                    action_start=round(action_start.time, 3) if action_start else None,
                    signals=list(phrase_signals),
                    confidence=1.0 if action_start else 0.85,
                )
            )

        pending_start = None
        action_start = None
        phrase_signals = []

    return clips

