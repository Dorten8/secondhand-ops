#!/usr/bin/env python3
"""transcribe.py — local Whisper transcription for a single audio file.

    python3 transcribe.py /path/to/audio.ogg [--lang da] [--model base]

Prints only the transcript to stdout; diagnostics to stderr so it can be piped.

WHY WE DECODE OURSELVES
faster-whisper 1.2.1 calls `av.open(..., metadata_errors="ignore")`, a kwarg that
PyAV 19 no longer accepts, so its own decode path raises TypeError on this box
and there is no ffmpeg binary to fall back to. Decoding with PyAV directly and
handing Whisper a float32 numpy array at 16 kHz skips that code path completely —
and skips the ffmpeg dependency too.

CPU int8. No GPU, no API key, no network once the model is cached.
"""
from __future__ import annotations

import argparse
import sys

SAMPLE_RATE = 16000


def decode_to_numpy(path: str):
    """Decode any container PyAV understands -> (float32 mono 16k, seconds)."""
    import av
    import numpy as np

    container = av.open(path)
    if not container.streams.audio:
        raise SystemExit(f"no audio stream in {path}")

    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    chunks = []
    for frame in container.decode(stream):
        for out in resampler.resample(frame):
            arr = out.to_ndarray()
            if arr.ndim > 1:
                arr = arr[0]                     # planar -> mono channel
            chunks.append(arr.astype("float32"))
    container.close()

    if not chunks:
        raise SystemExit("decoded zero samples")

    audio = np.concatenate(chunks) / 32768.0    # int16 -> -1..1
    return audio, len(audio) / SAMPLE_RATE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--model", default="base")
    ap.add_argument("--lang", default="")
    a = ap.parse_args()

    audio, secs = decode_to_numpy(a.path)
    print(f"decoded {secs:.1f}s @ {SAMPLE_RATE}Hz", file=sys.stderr)

    from faster_whisper import WhisperModel

    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio,
        language=a.lang or None,
        beam_size=5,
        vad_filter=True,          # skips silence, avoids hallucinated filler
    )
    print(f"lang={info.language} p={info.language_probability:.2f}", file=sys.stderr)

    text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
    print(text)
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())
