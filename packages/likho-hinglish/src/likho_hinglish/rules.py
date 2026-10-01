"""Rule-based Devanagari -> Hinglish transliteration.

Latin text passes through unchanged, so a mixed line such as "मुझे capsule चाहिए"
becomes "mujhe capsule chahiye". Words listed in a spelling table use that spelling
(the built-in table in words.py, overridden by the caller's own table); every other word
goes through three steps:

1. split      - the word becomes a list of units (consonant + vowel, or a vowel)
2. schwa drop - the inherent 'a' is removed where Hindi speakers do not say it
                (समझाने -> samjhane, not samajhaane)
3. render     - units become letters, with chat-style choices such as "aa" only in
                closed syllables (बात -> baat, करना -> karna) and "ein" for word-final ें
"""

import re
from collections.abc import Callable, Mapping

from likho_hinglish.words import WORDS

CONSONANTS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "ळ": "l",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    # precomposed nukta letters
    "क़": "q",
    "ख़": "kh",
    "ग़": "g",
    "ज़": "z",
    "ड़": "r",
    "ढ़": "rh",
    "फ़": "f",
    "य़": "y",
}
# nukta written as a combining mark (U+093C) after the base consonant
NUKTA_MAP = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}

VOWELS = {  # independent vowel letters
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "i",
    "उ": "u",
    "ऊ": "oo",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
    "ऍ": "e",
    "ऎ": "e",
    "ऒ": "o",
}
MATRAS = {  # dependent vowel signs ("aa" is refined by context when rendering)
    "ा": "aa",
    "ि": "i",
    "ी": "i",
    "ु": "u",
    "ू": "oo",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ॉ": "o",
    "ॅ": "e",
    "ॆ": "e",
    "ॊ": "o",
}
VIRAMA = "्"
NUKTA = "़"
NASALS = frozenset({"ं", "ँ"})
VISARGA = "ः"
DIGITS = {chr(0x966 + i): str(i) for i in range(10)}
MARKS = frozenset(MATRAS) | {VIRAMA, NUKTA, VISARGA} | NASALS

INHERENT = "a"
KIND_CONSONANT, KIND_VOWEL, KIND_OTHER = "C", "V", "X"

DEVANAGARI_WORD_RE = re.compile(r"[ऀ-ॣ०-ॿ]+")
INVISIBLE_RE = re.compile("[​‌‍﻿�]")
DEVANAGARI_CHAR = r"[ऀ-ॣ०-ॿ]"


class _Unit:
    """One consonant (with its vowel) or one independent vowel of a word."""

    __slots__ = ("base", "kind", "nasal", "vowel")

    def __init__(self, kind: str, base: str, vowel: str = "", nasal: str = "") -> None:
        self.kind = kind
        self.base = base
        self.vowel = vowel  # "" = bare consonant, "a" = inherent vowel, else the matra
        self.nasal = nasal  # "" or "n" (anusvara / chandrabindu) or "h" (visarga)

    @property
    def has_vowel(self) -> bool:
        return self.kind != KIND_OTHER and self.vowel != ""


def _split(word: str) -> list[_Unit]:
    units: list[_Unit] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        i += 1
        if ch in CONSONANTS:
            base = CONSONANTS[ch]
            if i < n and word[i] == NUKTA:
                base = NUKTA_MAP.get(ch, base)
                i += 1
            vowel = INHERENT
            if i < n and word[i] == VIRAMA:
                vowel = ""
                i += 1
            elif i < n and word[i] in MATRAS:
                vowel = MATRAS[word[i]]
                i += 1
            units.append(_Unit(KIND_CONSONANT, base, vowel))
        elif ch in VOWELS:
            units.append(_Unit(KIND_VOWEL, VOWELS[ch], VOWELS[ch]))
        elif ch in NASALS:
            if units:
                units[-1].nasal = "n"
        elif ch == VISARGA:
            if units:
                units[-1].nasal = "h"
        elif ch in DIGITS:
            units.append(_Unit(KIND_OTHER, DIGITS[ch]))
        elif ch in MARKS:
            continue  # stray mark with nothing to attach to
        else:
            units.append(_Unit(KIND_OTHER, ch))
    return units


def _drop_schwas(units: list[_Unit]) -> list[_Unit]:
    """Remove the inherent 'a' where it is not pronounced (Hindi schwa deletion)."""
    last = len(units) - 1
    for idx, unit in enumerate(units):
        if unit.kind != KIND_CONSONANT or unit.vowel != INHERENT or unit.nasal:
            continue  # a nasal sign keeps its vowel: सुरंजान -> suranjaan
        if idx == last:
            unit.vowel = ""  # word-final: लोग -> log
            continue
        prev_has_vowel = idx > 0 and units[idx - 1].has_vowel
        nxt = units[idx + 1]
        nxt_is_vowelled_consonant = nxt.kind == KIND_CONSONANT and nxt.vowel != ""
        # Between a vowelled syllable and a vowelled consonant the schwa goes
        # (समझाने -> samjhane), unless the next consonant is a word-final inherent 'a'
        # that will itself be dropped (समझ -> samajh, not smjh).
        nxt_is_final_inherent = idx + 1 == last and nxt.vowel == INHERENT
        if prev_has_vowel and nxt_is_vowelled_consonant and not nxt_is_final_inherent:
            unit.vowel = ""
    return units


def _render(units: list[_Unit]) -> str:
    out: list[str] = []
    last = len(units) - 1
    for idx, unit in enumerate(units):
        if unit.kind == KIND_OTHER:
            out.append(unit.base)
            continue
        prev_has_vowel = idx > 0 and units[idx - 1].has_vowel
        if unit.kind == KIND_VOWEL:
            text = unit.base
            if text == "aa" and idx > 0:
                text = "a"  # हुआ -> hua; only word-initial आ is "aa" (आप -> aap)
            elif text == "e" and prev_has_vowel:
                text = "ye"  # गए -> gaye, बताइए -> bataiye
        else:
            vowel = unit.vowel
            if vowel == "aa":
                # "aa" before a word-final bare consonant (बात -> baat, नाम -> naam),
                # otherwise a single "a" (करना -> karna, चाहते -> chahte)
                nxt = units[idx + 1] if idx < last else None
                closed = nxt is not None and idx + 1 == last and nxt.kind == KIND_CONSONANT and nxt.vowel == ""
                vowel = "aa" if closed else "a"
            text = unit.base + vowel
        if unit.nasal == "n":
            if text.endswith("e") and idx == last:
                text += "in"  # word-final: करें -> karein, बताएं -> batayein (but करेंगे -> karenge)
            else:
                nxt = units[idx + 1] if idx < last else None
                labial = nxt is not None and nxt.kind == KIND_CONSONANT and nxt.base[:1] in ("p", "b", "m")
                text += "m" if labial else "n"  # संभव -> sambhav, अंदर -> andar
        elif unit.nasal == "h":
            text += "h"
        out.append(text)
    return "".join(out)


def _literal(value: str) -> Callable[[re.Match[str]], str]:
    """A replacement that inserts value as it is; a plain string would treat backslashes as group references."""
    return lambda _match: value


class Transliterator:
    """Devanagari -> Hinglish with one spelling table.

    The table is the built-in one (words.WORDS) with the caller's entries on top. A key
    with spaces is a phrase: it is replaced as a whole, on word boundaries, before single
    words are converted. That fixes expressions a speech model mishears the same way
    every time. Longer phrases win over shorter ones.

    An instance is immutable and safe to share between threads and tasks.
    """

    def __init__(self, spellings: Mapping[str, str] | None = None) -> None:
        table = dict(WORDS)
        if spellings:
            table.update(spellings)
        self._words = {key: value for key, value in table.items() if " " not in key}
        phrases = sorted((key for key in table if " " in key), key=len, reverse=True)
        self._phrases = [
            (phrase, re.compile(rf"(?<!{DEVANAGARI_CHAR}){re.escape(phrase)}(?!{DEVANAGARI_CHAR})"), table[phrase])
            for phrase in phrases
        ]

    def word(self, word: str) -> str:
        """Transliterate one Devanagari word (no spaces)."""
        spelling = self._words.get(word)
        if spelling is not None:
            return spelling
        return _render(_drop_schwas(_split(word)))

    def text(self, text: str) -> str:
        """Return text with every Devanagari word replaced by its Hinglish spelling."""
        text = INVISIBLE_RE.sub("", text)
        text = text.replace("॥", ".").replace("।", ".")
        for phrase, pattern, spelling in self._phrases:
            if phrase in text:
                text = pattern.sub(_literal(spelling), text)
        return DEVANAGARI_WORD_RE.sub(lambda match: self.word(match.group(0)), text)


_DEFAULT = Transliterator()


def word_to_hinglish(word: str) -> str:
    """Transliterate one Devanagari word with the built-in spelling table."""
    return _DEFAULT.word(word)


def to_hinglish(text: str) -> str:
    """Transliterate text with the built-in spelling table."""
    return _DEFAULT.text(text)
