"""Tables owned by this service. Everything is scoped to a workspace."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GlossaryTerm(Base):
    """A name the speech model should listen for (product, person, herb), in the script of the audio."""

    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("workspace_id", "term", name="uq_glossary_workspace_term"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    term: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8), default="hi")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Spelling(Base):
    """How a word, or a whole phrase, must be written in Hinglish. Wins over the built-in rules."""

    __tablename__ = "spellings"
    __table_args__ = (UniqueConstraint("workspace_id", "source", name="uq_spelling_workspace_source"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    is_phrase: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LanguagePolicy(Base):
    """One rule of a workspace's language policy. Rules are checked in position order; the first match wins."""

    __tablename__ = "language_policies"
    __table_args__ = (UniqueConstraint("workspace_id", "position", name="uq_policy_workspace_position"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    position: Mapped[int] = mapped_column(Integer)
    # ISO 639-1 code, or "*" for any language.
    detected_language: Mapped[str] = mapped_column(String(8))
    min_probability: Mapped[float] = mapped_column(Float, default=0.0)
    decode_as: Mapped[str] = mapped_column(String(8))
    transliterate: Mapped[bool] = mapped_column(Boolean, default=True)


class VocabularyVersion(Base):
    """Counts every change to a workspace's glossary, spellings or policy, so callers can tell stale from fresh."""

    __tablename__ = "vocabulary_versions"

    workspace_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
