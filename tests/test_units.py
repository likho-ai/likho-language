"""Policy, ids, events and the seed file readers: no database or network needed."""

import json
import re
import time
from pathlib import Path

import jsonschema
import pytest

from likho_language import policy
from likho_language.events import vocabulary_updated
from likho_language.ids import new_id
from likho_language.policy import ANY_LANGUAGE, Decision, PolicyRule
from likho_language.seed import read_glossary, read_spellings

CONTRACTS = Path(__file__).parent / "contracts"


class TestPolicy:
    @pytest.mark.parametrize(
        ("detected", "probability", "expected"),
        [
            ("en", 0.95, Decision("en", False)),  # clearly English stays English
            ("en", 0.80, Decision("en", False)),  # the threshold itself counts
            ("en", 0.79, Decision("hi", True)),  # unsure English is treated as Hindi with English words
            ("hi", 0.91, Decision("hi", True)),
            ("ur", 0.99, Decision("hi", True)),  # Urdu is decoded as Hindi so the text is Devanagari
            ("pa", 0.60, Decision("hi", True)),
            (" EN ", 0.90, Decision("en", False)),  # code is trimmed and lower-cased
        ],
    )
    def test_default_rules(self, detected: str, probability: float, expected: Decision) -> None:
        assert policy.resolve(detected, probability) == expected

    def test_workspace_rules_are_checked_first_and_in_order(self) -> None:
        rules = (
            PolicyRule("ur", 0.90, "ur", False),
            PolicyRule("ur", 0.0, "hi", True),
            PolicyRule("en", 0.50, "en", False),
        )
        assert policy.resolve("ur", 0.95, rules) == Decision("ur", False)
        assert policy.resolve("ur", 0.40, rules) == Decision("hi", True)
        assert policy.resolve("en", 0.60, rules) == Decision("en", False)

    def test_falls_back_to_the_defaults_when_no_workspace_rule_matches(self) -> None:
        rules = (PolicyRule("ta", 0.5, "ta", False),)
        assert policy.resolve("hi", 0.9, rules) == Decision("hi", True)
        assert policy.resolve("en", 0.9, rules) == Decision("en", False)

    def test_a_catch_all_workspace_rule_overrides_the_defaults(self) -> None:
        rules = (PolicyRule(ANY_LANGUAGE, 0.0, "en", False),)
        assert policy.resolve("hi", 0.99, rules) == Decision("en", False)


class TestIds:
    def test_shape(self) -> None:
        assert re.fullmatch(r"spl_[0-9A-HJKMNP-TV-Z]{26}", new_id("spl"))

    def test_unique_and_sortable_by_time(self) -> None:
        first = new_id("gls")
        time.sleep(0.002)
        second = new_id("gls")
        assert first != second
        assert first < second
        assert len({new_id("x") for _ in range(2000)}) == 2000


class TestEvent:
    def test_vocabulary_updated_matches_the_contract(self) -> None:
        event = vocabulary_updated("wsp_01JB7Z5K3M9Q2W4X6Y8A0C1E3G", "spellings", 12)
        envelope = json.loads((CONTRACTS / "cloudevent.schema.json").read_text(encoding="utf-8"))
        data = json.loads((CONTRACTS / "likho.vocabulary.updated.v1.schema.json").read_text(encoding="utf-8"))
        checker = jsonschema.FormatChecker()
        jsonschema.Draft202012Validator(envelope, format_checker=checker).validate(event)
        jsonschema.Draft202012Validator(data, format_checker=checker).validate(event["data"])
        assert event["type"] == "likho.vocabulary.updated.v1"
        assert event["source"] == "likho-language"

    def test_every_event_has_its_own_id(self) -> None:
        assert vocabulary_updated("wsp_x", "glossary", 1)["id"] != vocabulary_updated("wsp_x", "glossary", 1)["id"]


class TestSeedFiles:
    def test_glossary_ignores_comments_and_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "glossary.txt"
        path.write_text("# names\nत्रिफला\n\n  अश्वगंधा  # a herb\n", encoding="utf-8")
        assert read_glossary(path) == ["त्रिफला", "अश्वगंधा"]

    def test_spellings_are_trimmed(self, tmp_path: Path) -> None:
        path = tmp_path / "spellings.json"
        path.write_text(json.dumps({" त्रिफला ": " Triphala "}, ensure_ascii=False), encoding="utf-8")
        assert read_spellings(path) == {"त्रिफला": "Triphala"}

    @pytest.mark.parametrize("content", ['["a"]', '{"a": 1}', '"text"'])
    def test_spellings_must_be_an_object_of_strings(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "spellings.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="expected a JSON object"):
            read_spellings(path)
