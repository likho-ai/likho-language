# likho-language

The language service of [Likho](https://github.com/likho-ai). It answers four questions for
the other services:

| Question | Call |
| --- | --- |
| How is this line written in Hinglish? | `Transliterate`, `TransliterateBatch` |
| Which names should the speech model listen for? | `GetHotwords` |
| The model detected Urdu with probability 0.9: which language do we decode as? | `ResolveDecodePolicy` |
| What are this workspace's glossary and spellings? | `List…`, `Upsert…`, `Delete…` for glossary terms and spellings |

The interface is `likho.language.v1.LanguageService` in
[likho-contracts](https://github.com/likho-ai/likho-contracts). Everything is scoped to a
workspace: two workspaces can spell the same word differently.

## How transliteration works

Speech models write Hindi and Urdu calls in Devanagari. This service turns that into Hinglish,
the Roman-letter Hindi people type in chat:

```
नमस्ते, आपका ऑर्डर कल तक पहुँच जाएगा   ->   namaste, aapka order kal tak pahunch jayega
```

1. **The workspace's spellings** win: `त्रिफला → Triphala`. A source with spaces is a phrase and
   is replaced as a whole, on word boundaries, longest first.
2. **The built-in table** (`src/likho_hinglish/words.py`) covers common words and English
   loanwords (`कैप्सूल → capsule`).
3. **The rules** (`src/likho_hinglish/rules.py`) handle every other word: schwa deletion
   (`समझाने → samjhane`), long vowels only in closed syllables (`बात → baat`, `करना → karna`),
   nasals, nukta letters.

Latin text, digits and punctuation pass through. Text in Arabic script is refused: the
language policy decodes Urdu calls as Hindi so that the text arrives in Devanagari.

`likho_hinglish` has no dependencies and can be used on its own:

```python
from likho_hinglish import Transliterator
Transliterator({"त्रिफला": "Triphala"}).text("त्रिफला लीजिए")   # 'Triphala lijiye'
```

## Run it

Needs the [likho-infra](https://github.com/likho-ai/likho-infra) stack (PostgreSQL and NATS).

```bash
uv sync
uv run likho-language            # gRPC on 5030, health on 4030; creates its tables on start
```

Load an existing glossary and spelling file into a workspace:

```bash
uv run likho-language-seed --workspace wsp_... --glossary glossary.txt --spellings custom_words.json
```

With Docker, on the stack's network:

```bash
docker build -t likho-language .
docker run --rm --network likho -p 5030:5030 -p 4030:4030 \
  -e DATABASE_URL=postgresql+asyncpg://likho_language:likho_language@postgres:5432/likho_language \
  -e NATS_URL=nats://nats:4222 likho-language
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GRPC_PORT` | `5030` | gRPC, including the standard health service |
| `HTTP_PORT` | `4030` | `GET /healthz` (alive), `GET /readyz` (the database answers) |
| `DATABASE_URL` | local stack, database `likho_language` | PostgreSQL through asyncpg |
| `NATS_URL` | `nats://localhost:4222` | Event bus |
| `MIGRATE_ON_START` | `true` | Create or update the tables at start |
| `VOCABULARY_TTL_SECONDS` | `2` | How long a running service uses a workspace's vocabulary before checking for a newer version |
| `LOG_LEVEL` | `INFO` | Logs are JSON, one object per line |

## Behaviour worth knowing

* **Versions.** Every change to a workspace's glossary or spellings raises its vocabulary
  version by one, in the same transaction. Every reply carries the version it used.
* **Events.** After a change the service publishes `likho.vocabulary.updated.v1` on NATS
  subject `likho.vocabulary.updated`. If the bus is down the change is still saved; callers
  notice the new version in the next reply.
* **Adding is idempotent.** Upserting without an id creates the entry, or updates the one
  with the same text. The seed command can therefore run twice.
* **`enabled`.** A new entry is always switched on. The flag in a request is used only when
  an id is given (a proto3 bool cannot tell "not set" from false).
* **Errors.** `INVALID_ARGUMENT` (missing workspace, empty text, a rename that would
  duplicate), `NOT_FOUND` (unknown id), `UNIMPLEMENTED` (Arabic script). The message says
  what was wrong.

## Data

PostgreSQL database `likho_language`, migrations in `src/likho_language/migrations`:
`glossary_terms`, `spellings`, `language_policies`, `vocabulary_versions`.

The default language policy, used until a workspace defines its own rules: English with
probability 0.80 or more is decoded as English and not transliterated; everything else,
including Urdu, is decoded as Hindi and transliterated.

## Work on it

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest                                  # 83 tests
uv run pytest -m "not integration"             # 59 tests that need no stack
```

The integration tests start the real service on free ports and call it over gRPC against
the stack's PostgreSQL and NATS. Without the stack they are skipped; with
`LIKHO_REQUIRE_STACK=1` (set in CI) they fail instead. Each test uses a workspace of its own
and removes it afterwards.

## Not here yet

* Calls to edit the language policy (the table exists; rules are read from it).
* Metrics (`/metrics`) and tracing.
* The contracts package has no `py.typed` marker yet, so its messages are untyped for mypy.
