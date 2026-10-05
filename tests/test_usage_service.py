"""Lines from the bus raise the counts; imports load many entries at once. Real PostgreSQL and NATS."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import nats
import pytest
from likho.language.v1 import language_pb2 as pb

from likho_language.ids import new_id
from likho_language.usage import SEGMENT_SUBJECT
from tests.conftest import Service

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _line(
    service: Service,
    workspace_id: str,
    index: int,
    text_script: str,
    text_roman: str,
    *,
    recording_id: str = "",
    when: str = "2026-10-05T07:00:00Z",
    with_workspace: bool = True,
) -> str:
    """Publishes one transcript line the way a worker does; returns the recording id."""
    recording_id = recording_id or new_id("rec")
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": new_id("evt"),
        "source": "likho-transcription",
        "type": "likho.transcription.segment.v1",
        "time": when,
        "subject": recording_id,
        "datacontenttype": "application/json",
        "data": {
            "job_id": new_id("job"),
            "recording_id": recording_id,
            "total_seconds": 60.0,
            "segment": {
                "index": index,
                "start_seconds": index * 2.0,
                "end_seconds": index * 2.0 + 1.5,
                "text_script": text_script,
                "text_roman": text_roman,
            },
        },
    }
    if with_workspace:
        event["data"]["workspace_id"] = workspace_id
    connection = await nats.connect(service.settings.nats_url)
    try:
        await connection.jetstream().publish(
            SEGMENT_SUBJECT, json.dumps(event, ensure_ascii=False).encode(), headers={"Nats-Msg-Id": event["id"]}
        )
    finally:
        await connection.close()
    return recording_id


async def _until[T](check: Callable[[], Awaitable[T | None]], what: str, within: float = 10.0) -> T:
    deadline = asyncio.get_running_loop().time() + within
    while True:
        value = await check()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


async def _glossary(service: Service, workspace: str) -> dict[str, pb.GlossaryTerm]:
    reply = await service.stub.ListGlossaryTerms(pb.ListGlossaryTermsRequest(workspace_id=workspace))
    return {t.term: t for t in reply.terms}


async def _spellings(service: Service, workspace: str) -> dict[str, pb.Spelling]:
    reply = await service.stub.ListSpellings(pb.ListSpellingsRequest(workspace_id=workspace))
    return {s.source: s for s in reply.spellings}


async def test_lines_heard_raise_the_counts_and_keep_the_last_examples(service: Service, workspace: str) -> None:
    await service.stub.UpsertGlossaryTerm(
        pb.UpsertGlossaryTermRequest(workspace_id=workspace, term=pb.GlossaryTerm(term="अश्वगंधा"))
    )
    await service.stub.UpsertGlossaryTerm(
        pb.UpsertGlossaryTermRequest(
            workspace_id=workspace, term=pb.GlossaryTerm(term="Triphala Churna", language="en")
        )
    )
    await service.stub.UpsertSpelling(
        pb.UpsertSpellingRequest(workspace_id=workspace, spelling=pb.Spelling(source="त्रिफला", target="Triphala"))
    )
    fresh = await _glossary(service, workspace)
    assert (fresh["अश्वगंधा"].heard, fresh["अश्वगंधा"].is_phrase, fresh["Triphala Churna"].is_phrase) == (0, False, True)
    assert not fresh["अश्वगंधा"].HasField("last_heard_at")

    recording = await _line(service, workspace, 0, "अश्वगंधा और त्रिफला लीजिए", "ashwagandha aur Triphala lijiye")
    terms = await _until(lambda: _heard(service, workspace, "अश्वगंधा", 1), "the herb to be heard once")
    assert terms["अश्वगंधा"].last_heard_at.ToDatetime().isoformat() == "2026-10-05T07:00:00"
    assert terms["Triphala Churna"].heard == 0  # not in that line
    spellings = await _spellings(service, workspace)
    spelt = spellings["त्रिफला"]
    assert (spelt.applied, len(spelt.examples)) == (1, 1)
    assert (spelt.examples[0].recording_id, spelt.examples[0].segment_index) == (recording, 0)
    assert (spelt.examples[0].before, spelt.examples[0].after) == (
        "अश्वगंधा और त्रिफला लीजिए",
        "ashwagandha aur Triphala lijiye",
    )

    # An English term is found in the Hinglish layer too, whatever the case; examples come newest first.
    await _line(
        service,
        workspace,
        1,
        "त्रिफला चूर्ण भी लीजिए",
        "triphala churna bhi lijiye",
        recording_id=recording,
        when="2026-10-05T07:01:00Z",
    )
    terms = await _until(lambda: _heard(service, workspace, "Triphala Churna", 1), "the English phrase to be heard")
    assert terms["अश्वगंधा"].heard == 1
    spelt = (await _spellings(service, workspace))["त्रिफला"]
    assert spelt.applied == 2
    assert [e.segment_index for e in spelt.examples] == [1, 0]

    # Only the last few examples are kept; a line without a workspace (an older worker) is left alone.
    await _line(service, workspace, 2, "त्रिफला", "Triphala", recording_id=recording, when="2026-10-05T07:02:00Z")
    await _line(
        service,
        workspace,
        3,
        "त्रिफला",
        "Triphala",
        recording_id=recording,
        when="2026-10-05T07:03:00Z",
        with_workspace=False,
    )
    await _line(service, workspace, 4, "त्रिफला", "Triphala", recording_id=recording, when="2026-10-05T07:04:00Z")
    spelt = await _until(lambda: _applied(service, workspace, "त्रिफला", 4), "four lines with the spelling")
    assert [e.segment_index for e in spelt.examples] == [4, 2, 1]
    assert spelt.last_applied_at.ToDatetime().isoformat() == "2026-10-05T07:04:00"

    # Deleting the spelling takes its examples with it; the term's count survives its edits.
    await service.stub.DeleteSpelling(pb.DeleteSpellingRequest(workspace_id=workspace, id=spelt.id))
    assert "त्रिफला" not in await _spellings(service, workspace)
    assert (await _glossary(service, workspace))["अश्वगंधा"].heard == 1


async def _heard(service: Service, workspace: str, term: str, count: int) -> dict[str, pb.GlossaryTerm] | None:
    terms = await _glossary(service, workspace)
    return terms if terms[term].heard >= count else None


async def _applied(service: Service, workspace: str, source: str, count: int) -> pb.Spelling | None:
    spelling = (await _spellings(service, workspace))[source]
    return spelling if spelling.applied >= count else None


async def test_imports_load_many_entries_in_one_version(service: Service, workspace: str) -> None:
    await service.stub.UpsertGlossaryTerm(
        pb.UpsertGlossaryTermRequest(workspace_id=workspace, term=pb.GlossaryTerm(term="अश्वगंधा"))
    )
    reply = await service.stub.ImportGlossaryTerms(
        pb.ImportGlossaryTermsRequest(
            workspace_id=workspace,
            terms=[
                pb.GlossaryTerm(term="अश्वगंधा", note="a herb", enabled=True),
                pb.GlossaryTerm(term=" नीम ", enabled=True),
                pb.GlossaryTerm(term="तुलसी", enabled=False),
                pb.GlossaryTerm(term="तुलसी", enabled=True, note="the last one wins"),
            ],
        )
    )
    assert (reply.added, reply.updated, reply.vocabulary_version) == (2, 1, 2)
    terms = await _glossary(service, workspace)
    assert {t: (e.enabled, e.note) for t, e in terms.items()} == {
        "अश्वगंधा": (True, "a herb"),
        "नीम": (True, ""),
        "तुलसी": (True, "the last one wins"),
    }
    hotwords = await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace))
    assert (sorted(hotwords.terms), hotwords.vocabulary_version) == (["अश्वगंधा", "तुलसी", "नीम"], 2)

    spelt = await service.stub.ImportSpellings(
        pb.ImportSpellingsRequest(
            workspace_id=workspace,
            spellings=[
                pb.Spelling(source="नीम", target="Neem", enabled=True),
                pb.Spelling(source="कल तक", target="by tomorrow", enabled=True),
            ],
        )
    )
    assert (spelt.added, spelt.updated, spelt.vocabulary_version) == (2, 0, 3)
    spellings = await _spellings(service, workspace)
    assert (spellings["कल तक"].is_phrase, spellings["नीम"].is_phrase) == (True, False)
    again = await service.stub.ImportSpellings(
        pb.ImportSpellingsRequest(
            workspace_id=workspace, spellings=[pb.Spelling(source="नीम", target="Neem leaf", enabled=False)]
        )
    )
    assert (again.added, again.updated, again.vocabulary_version) == (0, 1, 4)
    text = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=workspace, text="नीम कल तक"))
    # The disabled spelling is not applied: the built-in rules write नीम on their own.
    assert (text.text_roman, text.vocabulary_version) == ("nim by tomorrow", 4)

    # Nothing to import: nothing changes, no new version.
    empty = await service.stub.ImportGlossaryTerms(pb.ImportGlossaryTermsRequest(workspace_id=workspace))
    assert (empty.added, empty.updated, empty.vocabulary_version) == (0, 0, 0)
    assert (await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace))).vocabulary_version == 4
