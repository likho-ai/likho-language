"""A workspace's vocabulary: glossary, spellings and language policy.

VocabularyStore reads and writes the tables and keeps one ready-to-use Vocabulary per
workspace in memory. Every write bumps the workspace's version in the same transaction;
readers re-check that version at most every `ttl_seconds`, so a change made through
another instance of the service is picked up within that time.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from likho_hinglish import Transliterator
from likho_language.ids import new_id
from likho_language.models import GlossaryTerm, LanguagePolicy, Spelling, VocabularyVersion
from likho_language.policy import PolicyRule


class NotFoundError(LookupError):
    """The row does not exist in this workspace."""


class InvalidError(ValueError):
    """The request cannot be stored as given; the message says why."""


@dataclass(frozen=True)
class Vocabulary:
    """Everything needed to serve one workspace, built once per version."""

    version: int
    transliterator: Transliterator
    terms: tuple[tuple[str, str], ...]  # (term, language) of enabled glossary entries, oldest first
    rules: tuple[PolicyRule, ...]

    def hotwords(self, language: str = "") -> list[str]:
        """Glossary names for one language, or all of them when no language is given."""
        return [term for term, lang in self.terms if not language or lang == language]


@dataclass
class _Cached:
    vocabulary: Vocabulary
    checked_at: float


def _clean(value: str, what: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise InvalidError(f"{what} must not be empty")
    return value


class VocabularyStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], ttl_seconds: float = 2.0) -> None:
        self._sessions = sessions
        self._ttl = ttl_seconds
        self._cache: dict[str, _Cached] = {}

    # ------------------------------------------------------------------ reading
    async def get(self, workspace_id: str) -> Vocabulary:
        cached = self._cache.get(workspace_id)
        now = time.monotonic()
        if cached is not None and now - cached.checked_at < self._ttl:
            return cached.vocabulary
        async with self._sessions() as session:
            version = await self._version(session, workspace_id)
            if cached is not None and cached.vocabulary.version == version:
                cached.checked_at = now
                return cached.vocabulary
            vocabulary = await self._load(session, workspace_id, version)
        self._cache[workspace_id] = _Cached(vocabulary, now)
        return vocabulary

    @staticmethod
    async def _version(session: AsyncSession, workspace_id: str) -> int:
        version = await session.scalar(
            select(VocabularyVersion.version).where(VocabularyVersion.workspace_id == workspace_id)
        )
        return version or 0

    @staticmethod
    async def _load(session: AsyncSession, workspace_id: str, version: int) -> Vocabulary:
        spellings = (
            await session.execute(
                select(Spelling.source, Spelling.target).where(
                    Spelling.workspace_id == workspace_id, Spelling.enabled.is_(True)
                )
            )
        ).all()
        terms = (
            await session.execute(
                select(GlossaryTerm.term, GlossaryTerm.language)
                .where(GlossaryTerm.workspace_id == workspace_id, GlossaryTerm.enabled.is_(True))
                .order_by(GlossaryTerm.id)
            )
        ).all()
        policies = (
            await session.scalars(
                select(LanguagePolicy)
                .where(LanguagePolicy.workspace_id == workspace_id)
                .order_by(LanguagePolicy.position)
            )
        ).all()
        return Vocabulary(
            version=version,
            transliterator=Transliterator({source: target for source, target in spellings}),
            terms=tuple((term, language) for term, language in terms),
            rules=tuple(
                PolicyRule(p.detected_language, p.min_probability, p.decode_as, p.transliterate) for p in policies
            ),
        )

    async def list_glossary(self, workspace_id: str) -> Sequence[GlossaryTerm]:
        async with self._sessions() as session:
            return (
                await session.scalars(
                    select(GlossaryTerm).where(GlossaryTerm.workspace_id == workspace_id).order_by(GlossaryTerm.id)
                )
            ).all()

    async def list_spellings(self, workspace_id: str) -> Sequence[Spelling]:
        async with self._sessions() as session:
            return (
                await session.scalars(
                    select(Spelling).where(Spelling.workspace_id == workspace_id).order_by(Spelling.id)
                )
            ).all()

    # ------------------------------------------------------------------ writing
    async def _bump(self, session: AsyncSession, workspace_id: str) -> int:
        statement = (
            insert(VocabularyVersion)
            .values(workspace_id=workspace_id, version=1)
            .on_conflict_do_update(
                index_elements=[VocabularyVersion.workspace_id],
                set_={"version": VocabularyVersion.version + 1, "updated_at": func.now()},
            )
            .returning(VocabularyVersion.version)
        )
        version = await session.scalar(statement)
        assert version is not None
        self._cache.pop(workspace_id, None)
        return version

    async def upsert_glossary_term(
        self, workspace_id: str, term_id: str, term: str, language: str, enabled: bool, note: str
    ) -> tuple[GlossaryTerm, int]:
        """Create a term, or update the one with this id (or, without an id, the one with this text)."""
        term = _clean(term, "term")
        language = language.strip().lower() or "hi"
        async with self._sessions() as session, session.begin():
            if term_id:
                row = await session.scalar(
                    select(GlossaryTerm).where(GlossaryTerm.workspace_id == workspace_id, GlossaryTerm.id == term_id)
                )
                if row is None:
                    raise NotFoundError(f"glossary term {term_id} not found")
                clash = await session.scalar(
                    select(GlossaryTerm.id).where(
                        GlossaryTerm.workspace_id == workspace_id, GlossaryTerm.term == term, GlossaryTerm.id != term_id
                    )
                )
                if clash is not None:
                    raise InvalidError(f"another glossary term already reads {term!r}")
            else:
                row = await session.scalar(
                    select(GlossaryTerm).where(GlossaryTerm.workspace_id == workspace_id, GlossaryTerm.term == term)
                )
                if row is None:
                    row = GlossaryTerm(id=new_id("gls"), workspace_id=workspace_id)
                    session.add(row)
            # Without an id the caller is adding a name: it is switched on. A proto3 bool cannot tell
            # "not set" from false, so "enabled" is only taken from the request when an id is given.
            row.term, row.language, row.note = term, language, note.strip()
            row.enabled = enabled if term_id else True
            await session.flush()
            version = await self._bump(session, workspace_id)
            session.expunge(row)
        return row, version

    async def delete_glossary_term(self, workspace_id: str, term_id: str) -> int:
        async with self._sessions() as session, session.begin():
            deleted = await session.scalar(
                delete(GlossaryTerm)
                .where(GlossaryTerm.workspace_id == workspace_id, GlossaryTerm.id == term_id)
                .returning(GlossaryTerm.id)
            )
            if deleted is None:
                raise NotFoundError(f"glossary term {term_id} not found")
            return await self._bump(session, workspace_id)

    async def upsert_spelling(
        self, workspace_id: str, spelling_id: str, source: str, target: str, enabled: bool
    ) -> tuple[Spelling, int]:
        """Create a spelling, or update the one with this id (or, without an id, the one for this source)."""
        source = _clean(source, "source")
        target = _clean(target, "target")
        async with self._sessions() as session, session.begin():
            if spelling_id:
                row = await session.scalar(
                    select(Spelling).where(Spelling.workspace_id == workspace_id, Spelling.id == spelling_id)
                )
                if row is None:
                    raise NotFoundError(f"spelling {spelling_id} not found")
                clash = await session.scalar(
                    select(Spelling.id).where(
                        Spelling.workspace_id == workspace_id, Spelling.source == source, Spelling.id != spelling_id
                    )
                )
                if clash is not None:
                    raise InvalidError(f"another spelling already exists for {source!r}")
            else:
                row = await session.scalar(
                    select(Spelling).where(Spelling.workspace_id == workspace_id, Spelling.source == source)
                )
                if row is None:
                    row = Spelling(id=new_id("spl"), workspace_id=workspace_id)
                    session.add(row)
            row.source, row.target = source, target
            row.enabled = enabled if spelling_id else True  # same rule as for glossary terms
            row.is_phrase = " " in source  # decided here, not by the caller
            await session.flush()
            version = await self._bump(session, workspace_id)
            session.expunge(row)
        return row, version

    async def delete_spelling(self, workspace_id: str, spelling_id: str) -> int:
        async with self._sessions() as session, session.begin():
            deleted = await session.scalar(
                delete(Spelling)
                .where(Spelling.workspace_id == workspace_id, Spelling.id == spelling_id)
                .returning(Spelling.id)
            )
            if deleted is None:
                raise NotFoundError(f"spelling {spelling_id} not found")
            return await self._bump(session, workspace_id)

    async def delete_workspace(self, workspace_id: str) -> None:
        """Remove everything a workspace stored here (used by tests and when a workspace is deleted)."""
        async with self._sessions() as session, session.begin():
            for table in (GlossaryTerm, Spelling, LanguagePolicy, VocabularyVersion):
                await session.execute(delete(table).where(table.workspace_id == workspace_id))
        self._cache.pop(workspace_id, None)
