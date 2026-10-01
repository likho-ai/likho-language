"""Loads a glossary file and a spellings file into one workspace.

    likho-language-seed --workspace wsp_... --glossary glossary.txt --spellings custom_words.json

glossary.txt   one name per line; blank lines and text after '#' are ignored
spellings.json a JSON object {"देवनागरी": "hinglish"}; a key with spaces is a phrase

Running it again is safe: existing entries are updated, not duplicated.
"""

import argparse
import asyncio
import json
from pathlib import Path

from likho_language.db import make_engine, make_sessions, upgrade
from likho_language.settings import Settings
from likho_language.vocabulary import VocabularyStore


def read_glossary(path: Path) -> list[str]:
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def read_spellings(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f'{path}: expected a JSON object of {{"देवनागरी": "hinglish"}} pairs')
    return {key.strip(): value.strip() for key, value in data.items()}


async def seed(
    workspace_id: str, glossary: list[str], spellings: dict[str, str], settings: Settings
) -> tuple[int, int]:
    await asyncio.to_thread(upgrade, settings.database_url)
    engine = make_engine(settings.database_url)
    store = VocabularyStore(make_sessions(engine))
    try:
        for name in glossary:
            await store.upsert_glossary_term(workspace_id, "", name, "hi", True, "")
        for source, target in spellings.items():
            await store.upsert_spelling(workspace_id, "", source, target, True)
    finally:
        await engine.dispose()
    return len(glossary), len(spellings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="likho-language-seed", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workspace", required=True, help="workspace id the entries belong to")
    parser.add_argument("--glossary", type=Path, help="text file, one name per line")
    parser.add_argument("--spellings", type=Path, help='JSON file {"देवनागरी": "hinglish"}')
    args = parser.parse_args(argv)
    if not args.glossary and not args.spellings:
        parser.error("give --glossary, --spellings or both")

    glossary = read_glossary(args.glossary) if args.glossary else []
    spellings = read_spellings(args.spellings) if args.spellings else {}
    names, pairs = asyncio.run(seed(args.workspace, glossary, spellings, Settings()))
    print(f"workspace {args.workspace}: {names} glossary names and {pairs} spellings stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
