"""Finding terms and spellings in a line: no database or network needed."""

from datetime import UTC, datetime

from likho_language.usage import Matcher, heard_at


class TestMatcher:
    def test_finds_whole_words_and_phrases(self) -> None:
        matcher = Matcher([("herb", "त्रिफला"), ("when", "कल तक"), ("en", "Triphala")])
        assert matcher.find("ऑर्डर कल तक आएगा, त्रिफला लीजिए") == {"herb", "when"}
        assert matcher.find("Triphala lijiye") == {"en"}

    def test_a_term_inside_a_longer_word_is_not_a_hit(self) -> None:
        matcher = Matcher([("short", "त्रिफल")])
        assert matcher.find("त्रिफला लीजिए") == set()  # the vowel sign makes it another word
        assert matcher.find("त्रिफल लीजिए") == {"short"}

    def test_each_entry_is_found_on_its_own(self) -> None:
        matcher = Matcher([("one", "Triphala"), ("two", "Triphala Churna")])
        assert matcher.find("Triphala Churna lijiye") == {"one", "two"}

    def test_latin_matches_regardless_of_case_and_next_to_punctuation(self) -> None:
        matcher = Matcher([("en", "Triphala")])
        assert matcher.find("triphala, please") == {"en"}
        assert matcher.find("TRIPHALA.") == {"en"}
        assert matcher.find("Triphalax") == set()
        assert matcher.find("xTriphala") == set()

    def test_whitespace_is_normalised_on_both_sides(self) -> None:
        matcher = Matcher([("when", "कल   तक")])
        assert matcher.find("ऑर्डर कल\tतक  आएगा") == {"when"}

    def test_nothing_to_find(self) -> None:
        assert Matcher([]).find("कुछ भी") == set()
        assert len(Matcher([("blank", "   ")])) == 0
        assert Matcher([("herb", "त्रिफला")]).find("") == set()


class TestHeardAt:
    def test_the_events_time_is_used(self) -> None:
        assert heard_at({"time": "2026-10-05T07:00:00Z"}) == datetime(2026, 10, 5, 7, tzinfo=UTC)
        assert heard_at({"time": "2026-10-05T07:00:00.123+00:00"}).microsecond == 123000

    def test_otherwise_now(self) -> None:
        before = datetime.now(UTC)
        assert heard_at({}) >= before
        assert heard_at({"time": "yesterday"}) >= before
