"""Devanagari (Hindi/Urdu) -> Hinglish conversion.

    >>> to_hinglish("मुझे capsule चाहिए")
    'mujhe capsule chahiye'
    >>> Transliterator({"त्रिफला": "Triphala"}).text("त्रिफला लीजिए")
    'Triphala lijiye'

Spellings for common words and English loanwords live in words.py; everything else is
produced by the rules in rules.py. A Transliterator takes the caller's own spellings on
top of the built-in table, so two workspaces can spell the same word differently.
"""

from likho_hinglish.rules import Transliterator, to_hinglish, word_to_hinglish
from likho_hinglish.words import WORDS

__all__ = ["WORDS", "Transliterator", "to_hinglish", "word_to_hinglish"]
