"""Text as a voice should say it (ADR 0027).

What a reader skims, a voice reads out: "S. aureus", "2-4 mg/kg", "≥ 500",
"vs.". Lessons from the owner's earlier podcast project: expand symbols,
abbreviations, units and genus initials; strip what is visual; and keep any
respelling small, since respelling many words made speech choppier.
"""

from __future__ import annotations

import re

GENERA = {
    "S. aureus": "Staphylococcus aureus", "S. epidermidis": "Staphylococcus epidermidis",
    "S. lugdunensis": "Staphylococcus lugdunensis", "S. pyogenes": "Streptococcus pyogenes",
    "S. agalactiae": "Streptococcus agalactiae", "S. pneumoniae": "Streptococcus pneumoniae",
    "S. anginosus": "Streptococcus anginosus", "S. mitis": "Streptococcus mitis",
    "E. coli": "Escherichia coli", "E. faecalis": "Enterococcus faecalis", "E. faecium": "Enterococcus faecium",
    "E. cloacae": "Enterobacter cloacae", "K. pneumoniae": "Klebsiella pneumoniae", "K. oxytoca": "Klebsiella oxytoca",
    "P. aeruginosa": "Pseudomonas aeruginosa", "P. mirabilis": "Proteus mirabilis", "P. jirovecii": "Pneumocystis jirovecii",
    "A. baumannii": "Acinetobacter baumannii", "A. fumigatus": "Aspergillus fumigatus", "A. flavus": "Aspergillus flavus",
    "M. tuberculosis": "Mycobacterium tuberculosis", "M. avium": "Mycobacterium avium",
    "M. kansasii": "Mycobacterium kansasii", "M. abscessus": "Mycobacterium abscessus",
    "C. difficile": "Clostridioides difficile", "C. perfringens": "Clostridium perfringens",
    "C. neoformans": "Cryptococcus neoformans", "C. albicans": "Candida albicans", "C. auris": "Candida auris",
    "C. glabrata": "Candida glabrata", "H. influenzae": "Haemophilus influenzae", "H. pylori": "Helicobacter pylori",
    "N. meningitidis": "Neisseria meningitidis", "N. gonorrhoeae": "Neisseria gonorrhoeae",
    "L. monocytogenes": "Listeria monocytogenes", "L. pneumophila": "Legionella pneumophila",
    "B. fragilis": "Bacteroides fragilis", "B. henselae": "Bartonella henselae", "B. pertussis": "Bordetella pertussis",
    "B. cepacia": "Burkholderia cepacia", "T. gondii": "Toxoplasma gondii", "T. pallidum": "Treponema pallidum",
}

UNITS = (
    (r"mg/dL", "milligrams per deciliter"), (r"g/dL", "grams per deciliter"), (r"mg/kg", "milligrams per kilogram"),
    (r"mg/L", "milligrams per liter"), (r"mmol/L", "millimoles per liter"), (r"mEq/L", "milliequivalents per liter"),
    (r"IU/mL", "international units per milliliter"), (r"U/L", "units per liter"), (r"mL/min", "milliliters per minute"),
    (r"mL/kg", "milliliters per kilogram"), (r"cells/µL", "cells per microliter"), (r"cells/uL", "cells per microliter"),
    (r"mm\s?Hg", "millimeters of mercury"), (r"mcg", "micrograms"), (r"µg", "micrograms"), (r"mg", "milligrams"),
    (r"kg", "kilograms"), (r"mL", "milliliters"), (r"mmol", "millimoles"),
)
DOSING = (("q(\\d{1,2})h", r"every \1 hours"), ("bid", "twice daily"), ("tid", "three times daily"), ("qid", "four times daily"), ("qd", "daily"))
WORDS = (
    (r"\bvs\.?(?=\s)", "versus"), (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"), (r"\betc\.", "et cetera"),
    (r"\bapprox\.", "approximately"), (r"\bDr\.", "Doctor"), (r"\bet al\.", "and colleagues"),
)
SYMBOLS = (
    ("≥", " greater than or equal to "), ("≤", " less than or equal to "), (">=", " greater than or equal to "),
    ("<=", " less than or equal to "), ("±", " plus or minus "), ("→", " leads to "), ("×", " times "),
    ("~", " about "), ("&", " and "), ("%", " percent"), ("°C", " degrees Celsius"), ("°F", " degrees Fahrenheit"),
)


def speakable(text: str) -> str:
    out = str(text)
    out = re.sub(r"\[[^\]]*\]\([^)]*\)", lambda m: m.group(0)[1 : m.group(0).index("]")], out)  # [label](url)
    out = re.sub(r"[*_`#]+", "", out)
    out = re.sub(r"\[\d+(?:[-–,]\d+)*\]", "", out)
    for short, full in GENERA.items():
        out = re.sub(r"\b" + re.escape(short).replace(r"\ ", r"\s*"), full, out)
    out = re.sub(r"(\d)\s*[-–]\s*(\d)", r"\1 to \2", out)
    out = re.sub(r"(\d)\s*>\s*(\d)", r"\1 more than \2", out)
    out = re.sub(r"(?<=\s)>\s*(?=\d)", "more than ", out)
    out = re.sub(r"(?<=\s)<\s*(?=\d)", "less than ", out)
    for symbol, words in SYMBOLS:
        out = out.replace(symbol, words)
    for unit, words in UNITS:
        out = re.sub(r"(?<=\d)\s*" + unit + r"\b", " " + words, out)
    for pattern, words in DOSING:
        out = re.sub(r"\b" + pattern + r"\b", words, out)
    for pattern, words in WORDS:
        out = re.sub(pattern, words, out)
    out = re.sub(r"(\d)\s*/\s*(\d)", r"\1 over \2", out)  # 90/60
    out = out.replace("/", " ")  # TMP/SMX, and/or
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"([(\[]) ", r"\1", out)
    return re.sub(r" ([,.;:)\]])", r"\1", out).strip()
