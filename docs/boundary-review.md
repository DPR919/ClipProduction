# Boundary review: Orleans sample

User review of the original 27 clips in light_clips_tuned:

| Original clip | Reported issue |
| --- | --- |
| 002, 004, 007, 011, 013 | Excessive lead-in |
| 008, 026 | Two touches in the same clip |

The original exports used stream-copy seeking. Inspection of the first video
packet found presentation timestamps of -4.16s (008) and -4.28s (026), relative
to the requested clip start. Those packets retain earlier action; player behavior
depends on whether it honors the MP4 edit list. Accurate re-encoding removes them.

The revised motion algorithm looks for a sustained quiet setup followed by action,
uses a local motion threshold, and excludes footage before the previous light
event ends. It does not locate spoken En garde. Starts remain estimates with a
0.5-second preroll. A missing setup becomes an explicitly flagged lookback fallback.
Light onset anchors postroll; brightness peak remains diagnostic metadata.

Use refine-lights to preserve the original events and compare clip numbers.
The JSON comparisons array records old and new boundaries. Event detection was
held constant for this comparison; persistent graphics and replays can still
produce false events. Seven user labels are examples, not measured accuracy or
ground-truth timestamps. Revised clips still need user review.
