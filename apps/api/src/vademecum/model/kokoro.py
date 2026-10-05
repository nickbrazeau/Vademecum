"""Kokoro: natural on-device voices for the podcast (ADR 0027).

An open speech model (Apache 2.0) that runs on the Mac's own processor through
ONNX Runtime: no service, no key, nothing sent. It is optional. The package
comes from `scripts/voices.sh`, which also downloads the model and voice files
once, checked against their published digests, into the data directory's
`voices/` folder. Until then the Mac's own `say` voices are used.
"""

from __future__ import annotations

import importlib.util
import threading
import wave
from pathlib import Path

MODEL = "kokoro-v1.0.onnx"
VOICE_PACK = "voices-v1.0.bin"
ESPEAK_DATA = "espeak-ng-data"
PREFIX = "kokoro:"
RATE = 24000

# English voices, best first within each accent: (id, name, locale).
VOICES = (
    ("af_heart", "Heart", "en_US"),
    ("am_michael", "Michael", "en_US"),
    ("af_bella", "Bella", "en_US"),
    ("am_fenrir", "Fenrir", "en_US"),
    ("af_nicole", "Nicole", "en_US"),
    ("am_puck", "Puck", "en_US"),
    ("af_sarah", "Sarah", "en_US"),
    ("bf_emma", "Emma", "en_GB"),
    ("bm_george", "George", "en_GB"),
    ("bm_fable", "Fable", "en_GB"),
)
DEFAULTS = {"A": PREFIX + "af_heart", "B": PREFIX + "am_michael"}
# Each line is brought to the same loudness, so neither host is quieter.
TARGET_RMS = 0.085
PEAK_LIMIT = 0.95


def voices_dir_for(data_dir: Path) -> Path:
    return data_dir / "voices"


def ready(directory: Path | None) -> bool:
    if directory is None:
        return False
    files = (directory / MODEL, directory / VOICE_PACK, directory / ESPEAK_DATA / "phontab")
    return all(path.is_file() for path in files) and importlib.util.find_spec("kokoro_onnx") is not None


def listed(directory: Path | None) -> list[dict[str, str]]:
    if not ready(directory):
        return []
    accent = {"en_US": "US", "en_GB": "UK"}
    return [{"name": PREFIX + vid, "label": f"{name} (Kokoro, {accent[locale]})", "locale": locale} for vid, name, locale in VOICES]


class Engine:
    """One loaded model, shared; loading takes a few seconds and ~350 MB."""

    _lock = threading.Lock()
    _loaded: dict[Path, object] = {}

    @classmethod
    def get(cls, directory: Path):
        with cls._lock:
            engine = cls._loaded.get(directory)
            if engine is None:
                from kokoro_onnx import EspeakConfig, Kokoro
                import espeakng_loader

                # espeak keeps its data path in a short fixed buffer: the copy in
                # the voices folder has a short path, the package's own may not.
                config = EspeakConfig(lib_path=espeakng_loader.get_library_path(), data_path=str(directory / ESPEAK_DATA))
                engine = Kokoro(str(directory / MODEL), str(directory / VOICE_PACK), espeak_config=config)
                cls._loaded[directory] = engine
            return engine


def synth(directory: Path, text: str, voice: str, target: Path, rate: int = RATE) -> None:
    """Speak one line into a 16-bit mono WAV at `rate`."""
    import numpy as np

    engine = Engine.get(directory)
    voice_id = voice[len(PREFIX):] if voice.startswith(PREFIX) else voice
    lang = "en-gb" if voice_id.startswith("b") else "en-us"
    samples, produced = engine.create(text, voice=voice_id, speed=1.0, lang=lang)
    audio = np.asarray(samples, dtype=np.float32)
    if produced != rate and audio.size:
        positions = np.linspace(0, audio.size - 1, int(audio.size * rate / produced))
        audio = np.interp(positions, np.arange(audio.size), audio).astype(np.float32)
    rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
    if rms > 0:
        audio = audio * (TARGET_RMS / rms)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > PEAK_LIMIT:
        audio = audio * (PEAK_LIMIT / peak)
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
