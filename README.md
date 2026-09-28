# Bout Splitter

Split a full fencing bout recording into one clip per refereed phrase.

There are currently two detection paths:

- Audio/transcript path: anchors clips on `en garde` and `halt` / `halte`.
- Video-light path: finds scoring-light-like visual events and cuts candidate phrase clips around them.

The video-light path is currently more promising for broadcast videos where the commentator masks the referee.

## Install

From this directory:

```powershell
python -m pip install -e ".[all]"
```

Use `python -m bout_splitter` for commands. If your Python Scripts directory is on `PATH`, `bout-splitter` also works.

## Local Review And Upload

Start the review interface from this directory:

```powershell
python -m bout_splitter.review_app
```

Open `http://127.0.0.1:8765/` if the browser does not open automatically. Select a full bout recording, adjust the light-detection settings if needed, and generate clips. Review each clip in the browser and mark it usable or discarded. The score at touch is metadata, not a detection signal. Discarded clips are never uploaded.

In Review, drag the Begin and End handles on a clip's timeline to crop the generated clip. The change is saved when you release a handle; you can also seek in the video and use **Set Begin** or **Set End**. **Play selection** previews the chosen range, and **Reset** restores the full clip. The original generated clip stays intact so you can expand the crop later. Upload sends the cropped MP4 for usable clips; a clip already sent to S3 cannot be recropped.

Broadcast graphics vary by recording. In Generate, seek the source preview to a known touch, mark that time, and select separate red and green scoring-light areas. Set the bout start/end if the recording includes a long walkout or outro. The app calibrates its pixel threshold near the known touch and detects each light onset instead of treating a long-lived color as one event. If colored graphics dominate the sampled frames, analysis stops and asks for tighter areas or calibration. From an existing run, use **Re-analyze saved recording**; this creates a new run without copying the source video or changing earlier review decisions.

Enter the match details and the origin of the Whatsthecall site (for example, `http://localhost:3000` when it is running locally, or its deployed HTTPS URL), then sign in. The local tool uses the site's login, presign, and register endpoints; each approved MP4 goes directly to S3 using a presigned URL. Uploads can be retried after a failure without re-uploading already registered clips.

The source video, clips, and review state remain under the ignored `runs/` directory. Credentials are kept only in memory for the current review-app session. Stop the server with Ctrl+C. To use another local port, pass `--port 8766`.

## Probe A Video

```powershell
python -m bout_splitter probe "C:\path\to\bout.mp4"
```

## Split From A Transcript

Create a transcript JSON with timestamped segments:

```json
[
  {"start": 12.3, "end": 13.0, "text": "En garde"},
  {"start": 13.6, "end": 14.0, "text": "Allez"},
  {"start": 17.2, "end": 17.7, "text": "Halt"}
]
```

Then run:

```powershell
python -m bout_splitter split "C:\path\to\bout.mp4" --transcript transcript.json --out clips
```

## Split With Whisper

If `faster-whisper` is installed, omit `--transcript`:

```powershell
python -m bout_splitter split "C:\path\to\bout.mp4" --out clips --whisper-model small
```

The command writes:

- `clips/phrase_001.mp4`, `phrase_002.mp4`, ...
- `clips/detections.json`
- `clips/transcript.json` when transcription is generated

## Analyze Scoring Lights

Broadcast audio may emphasize the commentator instead of the referee. In that case, use video-light detection:

```powershell
python -m bout_splitter analyze-lights "C:\path\to\bout.mp4" --out light_analysis
```

This writes:

- `light_analysis/light_events.json`
- `light_analysis/light_debug_contact_sheet.jpg`

To cut candidate clips from detected light events:

```powershell
python -m bout_splitter split-lights "C:\path\to\bout.mp4" --out light_clips
```

Video clips are re-encoded for accurate starts by default. `--stream-copy` is
faster but may retain footage from an earlier keyframe, including a previous touch.
Motion starts now require a sustained quiet setup followed by action. They are
estimates, not verified timestamps for the referee's En garde command. Fallbacks
and other review reasons are recorded per clip in the analysis JSON.

To revise an existing run without redetecting its light events:

```powershell
python -m bout_splitter refine-lights light_clips_tuned/light_events.json --out light_clips_refined
```

Choose a fresh output folder. This preserves the original files and records
old-to-new clip numbers and boundaries in `comparisons` in `light_events.json`.
Add `--dry-run` to inspect ranges without exporting video. See
[boundary review](docs/boundary-review.md) for the reported examples and limitations.

Useful tuning options:

```powershell
--roi "0,0,1,0.72"         # normalized x1,y1,x2,y2 color-detection crop
--motion-roi "0,0,1,0.78"  # crop used to estimate motion starts
--colors red,green         # colors to detect
--fps 5                    # video samples per second
--min-pixels 80            # minimum bright color pixels to count as a light
--min-gap 1.5              # merge events separated by less than this many seconds
--start-at 70              # ignore walkout/intro
--end-at 590               # ignore outro
--lookback 8               # seconds to search backward or fixed-lookback amount
--start-mode motion        # or fixed-lookback
--post-roll 2              # seconds after the light event
```

The original Orleans run used these settings (the review found boundary errors):

```powershell
python -m bout_splitter split-lights "C:\Users\Peter\Videos\whatsthecall\WomensFinal-2425 Orléans Sabre Grand Prix.mp4" --out light_clips_tuned --colors red,green --roi "0,0.78,1,1" --motion-roi "0,0,1,0.78" --min-pixels 500 --min-gap 5 --start-at 70 --end-at 590 --start-mode fixed-lookback --lookback 8
```
