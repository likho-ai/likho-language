"""likho.language.v1.LanguageService over gRPC."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from likho.common.v1 import common_pb2
from likho.language.v1 import language_pb2, language_pb2_grpc

from likho_language import policy
from likho_language.events import Publisher
from likho_language.models import GlossaryTerm, Spelling, SpellingExample
from likho_language.vocabulary import InvalidError, NotFoundError, VocabularyStore

log = logging.getLogger(__name__)

# Whisper writes Urdu in the Arabic script when asked for Urdu; the Hinglish rules read Devanagari.
# The language policy therefore decodes Urdu calls as Hindi, and text in Arabic script is refused here.
_UNSUPPORTED = {common_pb2.SCRIPT_ARABIC: "Arabic script"}


def _when(value: datetime | None) -> Timestamp | None:
    if value is None:
        return None
    stamp = Timestamp()
    stamp.FromDatetime(value)
    return stamp


def _term(row: GlossaryTerm) -> language_pb2.GlossaryTerm:
    return language_pb2.GlossaryTerm(
        id=row.id,
        term=row.term,
        language=row.language,
        enabled=row.enabled,
        note=row.note,
        is_phrase=row.is_phrase,
        heard=row.heard,
        last_heard_at=_when(row.last_heard_at),
    )


def _example(row: SpellingExample) -> language_pb2.SpellingExample:
    return language_pb2.SpellingExample(
        recording_id=row.recording_id,
        segment_index=row.segment_index,
        before=row.before,
        after=row.after,
        heard_at=_when(row.heard_at),
    )


def _spelling(row: Spelling, examples: Sequence[SpellingExample] = ()) -> language_pb2.Spelling:
    return language_pb2.Spelling(
        id=row.id,
        source=row.source,
        target=row.target,
        is_phrase=row.is_phrase,
        enabled=row.enabled,
        applied=row.applied,
        last_applied_at=_when(row.last_applied_at),
        examples=[_example(example) for example in examples],
    )


class LanguageServicer(language_pb2_grpc.LanguageServiceServicer):
    def __init__(self, store: VocabularyStore, publisher: Publisher) -> None:
        self._store = store
        self._publisher = publisher

    # ------------------------------------------------------------------ helpers
    @staticmethod
    async def _workspace(request: Any, context: grpc.aio.ServicerContext) -> str:
        workspace_id = request.workspace_id.strip()
        if not workspace_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "workspace_id is required")
        return workspace_id

    @staticmethod
    async def _guard[T](context: grpc.aio.ServicerContext, call: Callable[[], Awaitable[T]]) -> T:
        """Run a store call and turn its errors into gRPC status codes."""
        try:
            return await call()
        except NotFoundError as error:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(error))
        except InvalidError as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        raise AssertionError("abort always raises")  # pragma: no cover

    @staticmethod
    async def _check_script(script: int, context: grpc.aio.ServicerContext) -> None:
        if script in _UNSUPPORTED:
            await context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                f"text in {_UNSUPPORTED[script]} cannot be transliterated; decode Urdu calls as Hindi",
            )

    # ------------------------------------------------------------------ transliteration
    async def Transliterate(
        self, request: language_pb2.TransliterateRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.TransliterateResponse:
        workspace_id = await self._workspace(request, context)
        await self._check_script(request.source_script, context)
        vocabulary = await self._store.get(workspace_id)
        text = (
            request.text
            if request.source_script == common_pb2.SCRIPT_LATIN
            else vocabulary.transliterator.text(request.text)
        )
        return language_pb2.TransliterateResponse(text_roman=text, vocabulary_version=vocabulary.version)

    async def TransliterateBatch(
        self, request: language_pb2.TransliterateBatchRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.TransliterateBatchResponse:
        workspace_id = await self._workspace(request, context)
        await self._check_script(request.source_script, context)
        vocabulary = await self._store.get(workspace_id)
        if request.source_script == common_pb2.SCRIPT_LATIN:
            texts = list(request.texts)
        else:
            texts = [vocabulary.transliterator.text(text) for text in request.texts]
        return language_pb2.TransliterateBatchResponse(texts_roman=texts, vocabulary_version=vocabulary.version)

    async def GetHotwords(
        self, request: language_pb2.GetHotwordsRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.GetHotwordsResponse:
        workspace_id = await self._workspace(request, context)
        vocabulary = await self._store.get(workspace_id)
        return language_pb2.GetHotwordsResponse(
            terms=vocabulary.hotwords(request.language.strip().lower()), vocabulary_version=vocabulary.version
        )

    async def ResolveDecodePolicy(
        self, request: language_pb2.ResolveDecodePolicyRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.ResolveDecodePolicyResponse:
        workspace_id = await self._workspace(request, context)
        if not 0.0 <= request.probability <= 1.0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "probability must be between 0 and 1")
        vocabulary = await self._store.get(workspace_id)
        decision = policy.resolve(request.detected, request.probability, vocabulary.rules)
        return language_pb2.ResolveDecodePolicyResponse(
            decode_as=decision.decode_as, transliterate=decision.transliterate
        )

    # ------------------------------------------------------------------ glossary
    async def ListGlossaryTerms(
        self, request: language_pb2.ListGlossaryTermsRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.ListGlossaryTermsResponse:
        workspace_id = await self._workspace(request, context)
        rows = await self._store.list_glossary(workspace_id)
        return language_pb2.ListGlossaryTermsResponse(terms=[_term(row) for row in rows])

    async def UpsertGlossaryTerm(
        self, request: language_pb2.UpsertGlossaryTermRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.UpsertGlossaryTermResponse:
        workspace_id = await self._workspace(request, context)
        term = request.term
        row, version = await self._guard(
            context,
            lambda: self._store.upsert_glossary_term(
                workspace_id, term.id, term.term, term.language, term.enabled, term.note
            ),
        )
        await self._publisher.vocabulary_updated(workspace_id, "glossary", version)
        return language_pb2.UpsertGlossaryTermResponse(term=_term(row))

    async def DeleteGlossaryTerm(
        self, request: language_pb2.DeleteGlossaryTermRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.DeleteGlossaryTermResponse:
        workspace_id = await self._workspace(request, context)
        version = await self._guard(context, lambda: self._store.delete_glossary_term(workspace_id, request.id))
        await self._publisher.vocabulary_updated(workspace_id, "glossary", version)
        return language_pb2.DeleteGlossaryTermResponse()

    async def ImportGlossaryTerms(
        self, request: language_pb2.ImportGlossaryTermsRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.ImportGlossaryTermsResponse:
        workspace_id = await self._workspace(request, context)
        entries = [(t.term, t.language, t.enabled, t.note) for t in request.terms]
        added, updated, version = await self._guard(context, lambda: self._store.import_glossary(workspace_id, entries))
        if added or updated:
            await self._publisher.vocabulary_updated(workspace_id, "glossary", version)
        return language_pb2.ImportGlossaryTermsResponse(added=added, updated=updated, vocabulary_version=version)

    # ------------------------------------------------------------------ spellings
    async def ListSpellings(
        self, request: language_pb2.ListSpellingsRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.ListSpellingsResponse:
        workspace_id = await self._workspace(request, context)
        rows = await self._store.list_spellings(workspace_id)
        examples = await self._store.list_examples(workspace_id)
        return language_pb2.ListSpellingsResponse(spellings=[_spelling(row, examples.get(row.id, ())) for row in rows])

    async def ImportSpellings(
        self, request: language_pb2.ImportSpellingsRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.ImportSpellingsResponse:
        workspace_id = await self._workspace(request, context)
        entries = [(s.source, s.target, s.enabled) for s in request.spellings]
        added, updated, version = await self._guard(
            context, lambda: self._store.import_spellings(workspace_id, entries)
        )
        if added or updated:
            await self._publisher.vocabulary_updated(workspace_id, "spellings", version)
        return language_pb2.ImportSpellingsResponse(added=added, updated=updated, vocabulary_version=version)

    async def UpsertSpelling(
        self, request: language_pb2.UpsertSpellingRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.UpsertSpellingResponse:
        workspace_id = await self._workspace(request, context)
        spelling = request.spelling
        row, version = await self._guard(
            context,
            lambda: self._store.upsert_spelling(
                workspace_id, spelling.id, spelling.source, spelling.target, spelling.enabled
            ),
        )
        await self._publisher.vocabulary_updated(workspace_id, "spellings", version)
        return language_pb2.UpsertSpellingResponse(spelling=_spelling(row))

    async def DeleteSpelling(
        self, request: language_pb2.DeleteSpellingRequest, context: grpc.aio.ServicerContext
    ) -> language_pb2.DeleteSpellingResponse:
        workspace_id = await self._workspace(request, context)
        version = await self._guard(context, lambda: self._store.delete_spelling(workspace_id, request.id))
        await self._publisher.vocabulary_updated(workspace_id, "spellings", version)
        return language_pb2.DeleteSpellingResponse()
