import unittest

from bout_splitter.models import TranscriptSegment, TranscriptWord
from bout_splitter.signals import detect_audio_signals, normalize_text


class SignalTests(unittest.TestCase):
    def test_normalize_text_removes_accents_and_punctuation(self):
        self.assertEqual(normalize_text("En-garde, Prêts? Allez!"), "en garde prets allez")

    def test_detect_audio_signals_from_words(self):
        segment = TranscriptSegment(
            start=10.0,
            end=13.0,
            text="En garde. Allez. Halt.",
            words=(
                TranscriptWord(start=10.1, end=10.3, text="En"),
                TranscriptWord(start=10.3, end=10.7, text="garde"),
                TranscriptWord(start=11.4, end=11.7, text="Allez"),
                TranscriptWord(start=12.5, end=12.8, text="Halt"),
            ),
        )

        signals = detect_audio_signals([segment])
        self.assertEqual(
            [(signal.kind, signal.time) for signal in signals],
            [
                ("en_garde", 10.1),
                ("allez", 11.4),
                ("halt", 12.5),
            ],
        )


if __name__ == "__main__":
    unittest.main()
