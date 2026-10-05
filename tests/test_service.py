"""The running service, called over gRPC, with real PostgreSQL and NATS."""

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

import grpc
import jsonschema
import nats
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from likho.common.v1 import common_pb2
from likho.language.v1 import language_pb2 as pb

from likho_language.seed import seed
from tests.conftest import Service

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

CONTRACTS = Path(__file__).parent / "contracts"


def term(text: str, **fields: object) -> pb.GlossaryTerm:
    return pb.GlossaryTerm(term=text, **fields)  # type: ignore[arg-type]


def spelling(source: str, target: str, **fields: object) -> pb.Spelling:
    return pb.Spelling(source=source, target=target, **fields)  # type: ignore[arg-type]


async def _http_status(port: int, path: str) -> int:
    def get() -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return int(response.status)
        except urllib.error.HTTPError as error:
            return error.code

    return await asyncio.to_thread(get)


async def test_health(service: Service) -> None:
    health = health_pb2_grpc.HealthStub(service.channel)
    reply = await health.Check(health_pb2.HealthCheckRequest(service="likho.language.v1.LanguageService"))
    assert reply.status == health_pb2.HealthCheckResponse.SERVING
    assert await _http_status(service.settings.http_port, "/healthz") == 200
    assert await _http_status(service.settings.http_port, "/readyz") == 200
    assert await _http_status(service.settings.http_port, "/metrics") == 200
    assert await _http_status(service.settings.http_port, "/other") == 404


async def test_transliterates_with_the_built_in_table(service: Service, workspace: str) -> None:
    reply = await service.stub.Transliterate(
        pb.TransliterateRequest(
            workspace_id=workspace, text="दवा दिन में दो बार लीजिए", source_script=common_pb2.SCRIPT_DEVANAGARI
        )
    )
    assert reply.text_roman == "dawa din mein do baar lijiye"
    assert reply.vocabulary_version == 0  # nothing stored for this workspace yet


async def test_a_new_spelling_changes_the_next_transliteration(service: Service, workspace: str) -> None:
    request = pb.TransliterateRequest(workspace_id=workspace, text="त्रिफला लीजिए")
    assert (await service.stub.Transliterate(request)).text_roman == "triphla lijiye"

    saved = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("त्रिफला", "Triphala"))
        )
    ).spelling
    assert saved.id.startswith("spl_")
    assert saved.enabled is True  # a new spelling is switched on even though the request left the flag unset
    assert saved.is_phrase is False

    reply = await service.stub.Transliterate(request)
    assert reply.text_roman == "Triphala lijiye"
    assert reply.vocabulary_version == 1


async def test_a_phrase_spelling_is_marked_as_a_phrase_and_applied(service: Service, workspace: str) -> None:
    saved = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("कल  तक", "by tomorrow"))
        )
    ).spelling
    assert saved.is_phrase is True
    assert saved.source == "कल तक"  # inner whitespace is normalised
    reply = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=workspace, text="ऑर्डर कल तक आएगा"))
    assert reply.text_roman == "order by tomorrow aayega"


async def test_workspaces_do_not_see_each_others_spellings(service: Service, workspace: str) -> None:
    other = workspace + "X"
    try:
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "medicine"))
        )
        mine = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=workspace, text="दवा"))
        theirs = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=other, text="दवा"))
        assert (mine.text_roman, theirs.text_roman) == ("medicine", "dawa")
        assert (await service.stub.ListSpellings(pb.ListSpellingsRequest(workspace_id=other))).spellings == []
    finally:
        await service.store.delete_workspace(other)


async def test_adding_the_same_source_twice_updates_instead_of_duplicating(service: Service, workspace: str) -> None:
    first = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "dawai"))
        )
    ).spelling
    second = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "medicine"))
        )
    ).spelling
    assert second.id == first.id
    listed = (await service.stub.ListSpellings(pb.ListSpellingsRequest(workspace_id=workspace))).spellings
    assert [(s.source, s.target) for s in listed] == [("दवा", "medicine")]


async def test_a_disabled_spelling_is_not_applied_and_deleting_restores_the_rules(
    service: Service, workspace: str
) -> None:
    saved = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "medicine"))
        )
    ).spelling
    request = pb.TransliterateRequest(workspace_id=workspace, text="दवा")

    await service.stub.UpsertSpelling(
        pb.UpsertSpellingRequest(
            workspace_id=workspace, spelling=spelling("दवा", "medicine", id=saved.id, enabled=False)
        )
    )
    assert (await service.stub.Transliterate(request)).text_roman == "dawa"

    await service.stub.UpsertSpelling(
        pb.UpsertSpellingRequest(
            workspace_id=workspace, spelling=spelling("दवा", "medicine", id=saved.id, enabled=True)
        )
    )
    assert (await service.stub.Transliterate(request)).text_roman == "medicine"

    await service.stub.DeleteSpelling(pb.DeleteSpellingRequest(workspace_id=workspace, id=saved.id))
    reply = await service.stub.Transliterate(request)
    assert reply.text_roman == "dawa"
    assert reply.vocabulary_version == 4  # create, disable, enable, delete


async def test_batch_keeps_order_and_length(service: Service, workspace: str) -> None:
    texts = ["नमस्ते", "", "hello", "ठीक है"]
    reply = await service.stub.TransliterateBatch(pb.TransliterateBatchRequest(workspace_id=workspace, texts=texts))
    assert list(reply.texts_roman) == ["namaste", "", "hello", "theek hai"]


async def test_latin_text_is_returned_unchanged_and_arabic_script_is_refused(service: Service, workspace: str) -> None:
    latin = await service.stub.Transliterate(
        pb.TransliterateRequest(workspace_id=workspace, text="Hello दवा", source_script=common_pb2.SCRIPT_LATIN)
    )
    assert latin.text_roman == "Hello दवा"
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await service.stub.Transliterate(
            pb.TransliterateRequest(workspace_id=workspace, text="سلام", source_script=common_pb2.SCRIPT_ARABIC)
        )
    assert error.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert "decode Urdu calls as Hindi" in (error.value.details() or "")


async def test_glossary_feeds_the_hotwords(service: Service, workspace: str) -> None:
    assert list((await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace))).terms) == []

    herb = (
        await service.stub.UpsertGlossaryTerm(
            pb.UpsertGlossaryTermRequest(workspace_id=workspace, term=term("अश्वगंधा", note="a herb"))
        )
    ).term
    await service.stub.UpsertGlossaryTerm(
        pb.UpsertGlossaryTermRequest(workspace_id=workspace, term=term("Triphala", language="en"))
    )
    assert herb.id.startswith("gls_")
    assert (herb.language, herb.enabled, herb.note) == ("hi", True, "a herb")

    everything = await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace))
    assert list(everything.terms) == ["अश्वगंधा", "Triphala"]
    assert everything.vocabulary_version == 2
    hindi = await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace, language="HI"))
    assert list(hindi.terms) == ["अश्वगंधा"]

    # switching a name off removes it from the hotwords but keeps it in the list
    await service.stub.UpsertGlossaryTerm(
        pb.UpsertGlossaryTermRequest(workspace_id=workspace, term=term("अश्वगंधा", id=herb.id, enabled=False))
    )
    assert list((await service.stub.GetHotwords(pb.GetHotwordsRequest(workspace_id=workspace))).terms) == ["Triphala"]
    listed = (await service.stub.ListGlossaryTerms(pb.ListGlossaryTermsRequest(workspace_id=workspace))).terms
    assert [(t.term, t.enabled) for t in listed] == [("अश्वगंधा", False), ("Triphala", True)]

    await service.stub.DeleteGlossaryTerm(pb.DeleteGlossaryTermRequest(workspace_id=workspace, id=herb.id))
    listed = (await service.stub.ListGlossaryTerms(pb.ListGlossaryTermsRequest(workspace_id=workspace))).terms
    assert [t.term for t in listed] == ["Triphala"]


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (lambda s, w: s.Transliterate(pb.TransliterateRequest(text="x")), grpc.StatusCode.INVALID_ARGUMENT),
        (
            lambda s, w: s.UpsertGlossaryTerm(pb.UpsertGlossaryTermRequest(workspace_id=w, term=term("   "))),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),
        (
            lambda s, w: s.UpsertSpelling(pb.UpsertSpellingRequest(workspace_id=w, spelling=spelling("दवा", ""))),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),
        (
            lambda s, w: s.UpsertSpelling(
                pb.UpsertSpellingRequest(workspace_id=w, spelling=spelling("दवा", "x", id="spl_missing"))
            ),
            grpc.StatusCode.NOT_FOUND,
        ),
        (
            lambda s, w: s.DeleteGlossaryTerm(pb.DeleteGlossaryTermRequest(workspace_id=w, id="gls_missing")),
            grpc.StatusCode.NOT_FOUND,
        ),
        (
            lambda s, w: s.DeleteSpelling(pb.DeleteSpellingRequest(workspace_id=w, id="spl_missing")),
            grpc.StatusCode.NOT_FOUND,
        ),
        (
            lambda s, w: s.ResolveDecodePolicy(
                pb.ResolveDecodePolicyRequest(workspace_id=w, detected="hi", probability=1.5)
            ),
            grpc.StatusCode.INVALID_ARGUMENT,
        ),
    ],
    ids=[
        "no-workspace",
        "empty-term",
        "empty-target",
        "unknown-spelling",
        "delete-unknown-term",
        "delete-unknown-spelling",
        "bad-probability",
    ],
)
async def test_bad_requests_get_a_clear_status(
    service: Service, workspace: str, call: object, code: grpc.StatusCode
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await call(service.stub, workspace)  # type: ignore[operator]
    assert error.value.code() == code
    assert error.value.details()  # the message says what was wrong


async def test_a_refused_change_does_not_bump_the_version(service: Service, workspace: str) -> None:
    await service.stub.UpsertSpelling(
        pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "dawai"))
    )
    second = (
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("पानी", "paani"))
        )
    ).spelling
    with pytest.raises(grpc.aio.AioRpcError) as error:  # renaming the second onto the first's source
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "x", id=second.id, enabled=True))
        )
    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    reply = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=workspace, text="दवा पानी"))
    assert (reply.text_roman, reply.vocabulary_version) == ("dawai paani", 2)


@pytest.mark.parametrize(
    ("detected", "probability", "expected"),
    [("en", 0.93, ("en", False)), ("en", 0.55, ("hi", True)), ("ur", 0.97, ("hi", True)), ("hi", 0.91, ("hi", True))],
)
async def test_default_language_policy(
    service: Service, workspace: str, detected: str, probability: float, expected: tuple[str, bool]
) -> None:
    reply = await service.stub.ResolveDecodePolicy(
        pb.ResolveDecodePolicyRequest(workspace_id=workspace, detected=detected, probability=probability)
    )
    assert (reply.decode_as, reply.transliterate) == expected


async def test_a_change_is_announced_on_the_event_bus(service: Service, workspace: str) -> None:
    connection = await nats.connect(service.settings.nats_url)
    try:
        subscription = await connection.jetstream().subscribe(
            "likho.vocabulary.updated", deliver_policy=nats.js.api.DeliverPolicy.NEW
        )
        await service.stub.UpsertSpelling(
            pb.UpsertSpellingRequest(workspace_id=workspace, spelling=spelling("दवा", "dawai"))
        )

        event = None
        for _ in range(50):  # other tests publish on the same subject; find ours
            message = await subscription.next_msg(timeout=5)
            candidate = json.loads(message.data)
            if candidate["data"]["workspace_id"] == workspace:
                event, headers = candidate, message.headers
                break
        assert event is not None, "no event for this workspace"
    finally:
        await connection.close()

    checker = jsonschema.FormatChecker()
    envelope = json.loads((CONTRACTS / "cloudevent.schema.json").read_text(encoding="utf-8"))
    data = json.loads((CONTRACTS / "likho.vocabulary.updated.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(envelope, format_checker=checker).validate(event)
    jsonschema.Draft202012Validator(data, format_checker=checker).validate(event["data"])
    assert event["data"] == {"workspace_id": workspace, "kind": "spellings", "version": 1}
    assert headers is not None and headers["Nats-Msg-Id"] == event["id"]


async def test_seed_loads_files_and_can_run_twice(service: Service, workspace: str) -> None:
    glossary = ["त्रिफला", "अश्वगंधा"]
    spellings = {"त्रिफला": "Triphala", "कल तक": "by tomorrow"}
    for _ in range(2):
        assert await seed(workspace, glossary, spellings, service.settings) == (2, 2)

    assert [
        t.term
        for t in (await service.stub.ListGlossaryTerms(pb.ListGlossaryTermsRequest(workspace_id=workspace))).terms
    ] == glossary
    stored = (await service.stub.ListSpellings(pb.ListSpellingsRequest(workspace_id=workspace))).spellings
    assert {s.source: (s.target, s.is_phrase) for s in stored} == {
        "त्रिफला": ("Triphala", False),
        "कल तक": ("by tomorrow", True),
    }
    reply = await service.stub.Transliterate(pb.TransliterateRequest(workspace_id=workspace, text="त्रिफला कल तक आएगा"))
    assert reply.text_roman == "Triphala by tomorrow aayega"
