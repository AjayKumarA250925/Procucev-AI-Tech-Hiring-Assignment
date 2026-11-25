"""Download the open-source Piper voices used for text-to-speech.

Run at Docker build time so the voices ship inside the image:
    python scripts/download_voices.py /app/.cache/piper
"""
import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
VOICES = [
    "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium",
    "hi/hi_IN/pratham/medium/hi_IN-pratham-medium",
    "en/en_US/lessac/medium/en_US-lessac-medium",
]

target = Path(sys.argv[1] if len(sys.argv) > 1 else ".cache/piper")
target.mkdir(parents=True, exist_ok=True)
for voice in VOICES:
    for ext in (".onnx.json", ".onnx"):
        out = target / (voice.rsplit("/", 1)[1] + ext)
        if not out.exists():
            print("downloading", out.name, flush=True)
            urllib.request.urlretrieve(BASE + voice + ext, out)
