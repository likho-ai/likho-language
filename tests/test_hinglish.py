"""The Devanagari -> Hinglish rules and the built-in spelling table."""

import pytest

from likho_hinglish import WORDS, Transliterator, to_hinglish, word_to_hinglish


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # spelling table
        ("में", "mein"),
        ("नहीं", "nahi"),
        ("कैप्सूल", "capsule"),
        ("टीवी", "TV"),
        ("मिनट", "minute"),
        ("सिर्फ", "sirf"),  # Urdu loan: "f", not the native "ph"
        ("तकलीफ़", "takleef"),
        # schwa deletion
        ("समझाने", "samjhane"),
        ("समझ", "samajh"),
        ("कितने", "kitne"),
        ("लोग", "log"),
        # long "aa" only in closed syllables
        ("बात", "baat"),
        ("नाम", "naam"),
        ("करना", "karna"),
        ("चाहते", "chahte"),
        ("आदमी", "aadmi"),
        # nasal signs
        ("सुरंजान", "suranjaan"),
        ("अश्वगंधा", "ashvagandha"),
        ("करेंगे", "karenge"),
        ("बताएं", "batayein"),
        ("संभव", "sambhav"),
        # vowel sequences
        ("गए", "gaye"),
        ("भाई", "bhai"),
        ("जाओ", "jao"),
        # nukta letters
        ("ज़रूर", "zaroor"),
        ("मर्ज़", "marz"),
        ("पढ़ना", "parhna"),
    ],
)
def test_word(word: str, expected: str) -> None:
    assert word_to_hinglish(word) == expected


@pytest.mark.parametrize(
    ("devanagari", "expected"),
    [
        ("नमस्ते, आपका ऑर्डर कल तक पहुँच जाएगा", "namaste, aapka order kal tak pahunch jayega"),
        ("क्या आप दो मिनट बात कर सकते हैं", "kya aap do minute baat kar sakte hain"),
        ("दवा दिन में दो बार खाने के बाद लीजिए", "dawa din mein do baar khane ke baad lijiye"),
        ("आपका नंबर और पता बता दीजिए", "aapka number aur pata bata dijiye"),
        ("ठीक है, धन्यवाद। फिर मिलेंगे", "theek hai, dhanyavaad. phir milenge"),
    ],
)
def test_sentence(devanagari: str, expected: str) -> None:
    assert to_hinglish(devanagari) == expected


def test_latin_text_and_punctuation_pass_through() -> None:
    assert to_hinglish("aap already कैप्सूल भी खा रहे हैं?") == "aap already capsule bhi kha rahe hain?"
    assert to_hinglish("Hello, world!") == "Hello, world!"
    assert to_hinglish("") == ""


def test_digits_and_danda() -> None:
    assert to_hinglish("२०२६ में।") == "2026 mein."


def test_invisible_characters_are_removed() -> None:
    zero_width_space, replacement_char = "​", "�"
    assert to_hinglish(f"ठीक{zero_width_space} है{replacement_char}") == "theek hai"


class TestTransliterator:
    def test_own_spelling_wins_over_the_rules_and_the_built_in_table(self) -> None:
        assert word_to_hinglish("त्रिफला") == "triphla"  # what the rules produce
        own = Transliterator({"त्रिफला": "Triphala", "नहीं": "nahin"})
        assert own.text("त्रिफला नहीं है") == "Triphala nahin hai"

    def test_two_tables_do_not_affect_each_other_or_the_built_in_one(self) -> None:
        first = Transliterator({"दवा": "dawai"})
        second = Transliterator({"दवा": "medicine"})
        assert first.word("दवा") == "dawai"
        assert second.word("दवा") == "medicine"
        assert to_hinglish("दवा") == "dawa"
        assert WORDS["दवा"] == "dawa"

    def test_a_phrase_is_replaced_as_a_whole_and_only_on_word_boundaries(self) -> None:
        own = Transliterator({"कल तक": "by tomorrow"})
        assert own.text("ऑर्डर कल तक आएगा") == "order by tomorrow aayega"
        assert own.text("कल तकिया लाना") == "kal takiya lana"  # "तक" inside a longer word is untouched

    def test_the_longer_phrase_wins(self) -> None:
        own = Transliterator({"कल तक": "SHORT", "कल तक पहुँच": "LONG"})
        assert own.text("कल तक पहुँच जाएगा") == "LONG jayega"
        assert own.text("कल तक आएगा") == "SHORT aayega"

    def test_a_spelling_is_inserted_literally(self) -> None:
        own = Transliterator({"एक दो": r"1\2 \g<0>"})  # would be a group reference in a regex replacement
        assert own.text("एक दो है") == r"1\2 \g<0> hai"
