"""Medical pronunciations for the podcast's Kokoro voices (feedback of 10 October).

Kokoro reads a line through espeak, which guesses at words it does not know: Sjögren
began with the letter S, Chvostek with "C H", syncope lost a syllable, CABG was not
"cabbage". Each entry here gives the word's sounds in the notation espeak itself
produces, and they are spliced into the line before the voice speaks it. Everything
else in the line is left to espeak as before.

The owner adds words of their own in the data folder's ``voices/lexicon.tsv``: one per
line, the word, a tab, then either a sound-alike spelling ("fyoo roh seh mide"), which
espeak turns into sounds, or the sounds themselves between slashes ("/fjʊˈɹoʊsəmaɪd/").
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FILE = "lexicon.tsv"

# Word (any case) -> sounds, in espeak's own symbols with ˈ for the stressed syllable.
BUILT_IN: dict[str, str] = {
    # Eponyms
    "Sjögren": "ʃˈoʊɡɹən",
    "Sjogren": "ʃˈoʊɡɹən",
    "Chvostek": "vˈɑːstɛk",
    "Trousseau": "tɹuːsˈoʊ",
    "Kussmaul": "kˈʊsmaʊl",
    "Virchow": "fˈɪɹkoʊ",
    "Behçet": "bɛtʃˈɛt",
    "Behcet": "bɛtʃˈɛt",
    "Guillain-Barré": "ɡiːjˈæn bɑːɹˈeɪ",
    "Guillain-Barre": "ɡiːjˈæn bɑːɹˈeɪ",
    "Budd-Chiari": "bˈʌd kiˈɑːɹi",
    "Takotsubo": "tˌɑːkoʊtsˈuːboʊ",
    "Wernicke": "vˈɛɹnɪkə",
    "Brugada": "bɹuːɡˈɑːdə",
    "Raynaud": "ɹeɪnˈoʊ",
    "Duchenne": "duːʃˈɛn",
    "Cheyne-Stokes": "tʃˈeɪn stˈoʊks",
    "Mallory-Weiss": "mˈæləɹi vˈaɪs",
    # Organisms
    "Coxsackie": "kɑːksˈɑːki",
    "jirovecii": "jiːɹoʊvˈɛtsiaɪ",
    "Clostridioides": "klɑːstɹˌɪdiˈɔɪdiːz",
    "difficile": "dɪfɪsˈiːl",
    "Strongyloides": "stɹˌɑːndʒəlˈɔɪdiːz",
    "Coccidioides": "kɑːksˌɪdiˈɔɪdiːz",
    "Haemophilus": "hiːmˈɑːfɪləs",
    "Pseudomonas": "suːdˈoʊmənəs",
    "Neisseria": "naɪsˈɪɹiə",
    "Klebsiella": "klɛbziˈɛlə",
    # Drugs
    "furosemide": "fjʊɹˈoʊsəmaɪd",
    "ceftriaxone": "sˌɛftɹaɪˈæksoʊn",
    "fomepizole": "foʊmˈɛpɪzoʊl",
    "baloxavir": "bəlˈɑːksəvɪɹ",
    "praziquantel": "pɹˌæzɪkwˈɑːntɛl",
    "leucovorin": "lˌuːkoʊvˈɔːɹɪn",
    "oseltamivir": "ˌɑːsəltˈæmɪvɪɹ",
    "meropenem": "mˌɛɹoʊpˈɛnəm",
    "sacubitril": "səkjˈuːbɪtɹɪl",
    "empagliflozin": "ˌɛmpəɡlɪflˈoʊzɪn",
    "dapagliflozin": "dˌæpəɡlɪflˈoʊzɪn",
    "apixaban": "əpˈɪksəbæn",
    "rivaroxaban": "ɹˌɪvəɹˈɑːksəbæn",
    "amiodarone": "ˌæmiˈoʊdəɹoʊn",
    # Signs and terms espeak gets wrong
    "syncope": "sˈɪŋkəpi",
    "ascites": "əsˈaɪtiːz",
    "pruritus": "pɹʊɹˈaɪtəs",
    "dyspnea": "dɪspnˈiːə",
    "pheochromocytoma": "fˌiːoʊkɹˌoʊmoʊsaɪtˈoʊmə",
    # Abbreviations said as words on the wards
    "MRSA": "mˈɜːsə",
    "HFrEF": "hˈɛfɹɛf",
    "HFpEF": "hˈɛfpɛf",
    "CABG": "kˈæbɪdʒ",
    "DOAC": "dˈiːoʊk",
}


def _parse_owner(path: Path) -> dict[str, str]:
    """The owner's own words: tab-separated, a spelling or /sounds/ after the word."""
    found: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        if "\t" not in line or line.lstrip().startswith("#"):
            continue
        word, said = (part.strip() for part in line.split("\t", 1))
        if word and said:
            found[word] = said
    return found


def owner_entries(voices_dir: Path | None) -> dict[str, str]:
    return {} if voices_dir is None else _parse_owner(voices_dir / FILE)


def save_owner_entries(voices_dir: Path, entries: dict[str, str]) -> None:
    voices_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Your own pronunciations: the word, a tab, then a spelling or /sounds/."]
    lines += [f"{word}\t{said}" for word, said in sorted(entries.items(), key=lambda item: item[0].casefold())]
    (voices_dir / FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pattern(words: list[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    ordered = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(word) for word in ordered) + r")(?![\w-])", re.IGNORECASE)


def spliced(tokenizer: Any, text: str, lang: str, owner: dict[str, str] | None = None) -> str | None:
    """The line as sounds, with every lexicon word's own sounds in place; None when the
    line holds no lexicon word, so it is spoken exactly as before."""
    entries = {**{key.casefold(): value for key, value in BUILT_IN.items()}}
    owned = {key.casefold(): value for key, value in (owner or {}).items()}
    entries.update(owned)
    pattern = _pattern(list(BUILT_IN) + list(owner or {}))
    if pattern is None or not pattern.search(text):
        return None
    pieces: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        before = text[last:match.start()]
        if before.strip():
            pieces.append(tokenizer.phonemize(before, lang))
        said = entries.get(match.group(1).casefold(), "")
        if said.startswith("/") and said.endswith("/") and len(said) > 2:
            sounds = said[1:-1]
        elif match.group(1).casefold() in owned:
            # A sound-alike spelling: espeak reads it, run together as one word.
            sounds = tokenizer.phonemize(said, lang).replace(" ", "")
        else:
            sounds = said
        pieces.append(tokenizer.known(sounds) if hasattr(tokenizer, "known") else sounds)
        last = match.end()
    after = text[last:]
    if after.strip():
        pieces.append(tokenizer.phonemize(after, lang))
    return re.sub(r"\s+([,.;:!?])", r"\1", " ".join(piece for piece in pieces if piece))


def _hear_in_process(voices_dir: str, text: str, voice: str, target: str) -> None:
    from . import kokoro

    kokoro.synth(Path(voices_dir), text, voice, Path(target))


async def hear(voices_dir: Path, word: str, voice: str, target: Path) -> None:
    """A short sample of a word as the podcast will say it, made in a process of its own
    so the model's weight never sits in the server."""
    import asyncio
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        await loop.run_in_executor(pool, _hear_in_process, str(voices_dir), f"{word}. {word}.", voice, str(target))
