# likho-language

The language service of [Likho](https://github.com/likho-ai). It answers five questions for
the other services:

| Question | Call |
| --- | --- |
| How is this line written in Hinglish? | `Transliterate`, `TransliterateBatch` |
| Which names should the speech model listen for? | `GetHotwords` |
| The model detected Urdu with probability 0.9: which language do we decode as? | `ResolveDecodePolicy` |
| What are this workspace's glossary and spellings? | `List…`, `Upsert…`, `Delete…` for glossary terms and spellings; `ImportGlossaryTerms`, `ImportSpellings` for many at once |
| How often was each of them heard? | `heard` / `applied` and the last lines on each entry of `ListGlossaryTerms` / `ListSpellings` |

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
2. **The built-in table** (`packages/likho-hinglish/src/likho_hinglish/words.py`) covers common words and English
   loanwords (`कैप्सूल → capsule`).
3. **The rules** (`packages/likho-hinglish/src/likho_hinglish/rules.py`) handle every other word: schwa deletion
   (`समझाने → samjhane`), long vowels only in closed syllables (`बात → baat`, `करना → karna`),
   nasals, nukta letters.

Latin text, digits and punctuation pass through. Text in Arabic script is refused: the
language policy decodes Urdu calls as Hindi so that the text arrives in Devanagari.

`likho_hinglish` is its own package (`packages/likho-hinglish`) with no dependencies, so the speech
engine can use the same rules offline:

```python
from likho_hinglish import Transliterator

Transliterator({"त्रिफला": "Triphala"}).text("त्रिफला लीजिए")  # 'Triphala lijiye'
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

Settings come from environment variables and from `.env` files chosen by `LIKHO_ENV`
(`development` by default). The files are read in this order, each overriding the one before,
and a real environment variable wins over all of them:

```
.env   .env.local   .env.<LIKHO_ENV>   .env.<LIKHO_ENV>.local
```

`.env.development`, `.env.staging` and `.env.production` are committed and hold no secrets.
`.env.<env>.local` holds the secrets of that environment on your machine; git ignores it, and
`likho-infra/scripts/make-env-secrets.py` makes it. In Kubernetes the same values come from
ConfigMaps and Secrets.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GRPC_PORT` | `5030` | gRPC, including the standard health service |
| `HTTP_PORT` | `4030` | `GET /healthz` (alive), `GET /readyz` (the database answers) |
| `DATABASE_URL` | local stack, database `likho_language` | PostgreSQL through asyncpg |
| `NATS_URL` | `nats://localhost:4222` | Event bus |
| `NATS_CONNECT_TIMEOUT_SECONDS` | `120` | How long the start keeps trying to reach NATS before going on without it |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Also push the metrics there (OTLP/HTTP); `GET /metrics` (calls by method and outcome with how long they took) is always on |
| `MIGRATE_ON_START` | `true` | Create or update the tables at start |
| `VOCABULARY_TTL_SECONDS` | `2` | How long a running service uses a workspace's vocabulary before checking for a newer version |
| `CONSUMERS_ENABLED` | `true` | Count the terms and spellings heard in the lines the workers publish |
| `SEGMENT_DURABLE` | `likho-language-segment` | The durable consumer of `likho.live.segment`; instances with the same name share the lines |
| `SEGMENT_START` | `all` | `new` starts at the lines published from now on |
| `EXAMPLES_PER_SPELLING` | `3` | How many of the last lines a spelling was applied to are kept |
| `LOG_LEVEL` | `INFO` | Logs are JSON, one object per line |

## Behaviour worth knowing

* **Versions.** Every change to a workspace's glossary or spellings raises its vocabulary
  version by one, in the same transaction. Every reply carries the version it used.
* **Events.** After a change the service publishes `likho.vocabulary.updated.v1` on NATS
  subject `likho.vocabulary.updated`. If the bus is down the change is still saved; callers
  notice the new version in the next reply.
* **Adding is idempotent.** Upserting without an id creates the entry, or updates the one
  with the same text. The seed command can therefore run twice. An import (`ImportGlossaryTerms`,
  `ImportSpellings`) does the same for many entries in one transaction: one new version, one event.
* **Counts.** Every transcript line the workers publish (`likho.live.segment`, with the
  recording's workspace) is read by a durable consumer. A glossary term found in the line as a
  whole word or phrase, in either layer, regardless of case, raises its `heard`; a spelling whose
  source is in the line raises its `applied` and keeps the line as one of its last few before/after
  examples. Counts are lines heard: a recording transcribed twice counts twice, and a line
  redelivered after a crash may count twice. They guide a person; they are not an audit.
* **Phrases.** A glossary term or a spelling source with spaces is a phrase (`is_phrase`), matched
  as a whole; a phrase glossary term reaches the speech model as a multi-word hotword.
* **`enabled`.** A new entry is always switched on. The flag in a request is used only when
  an id is given (a proto3 bool cannot tell "not set" from false).
* **Errors.** `INVALID_ARGUMENT` (missing workspace, empty text, a rename that would
  duplicate), `NOT_FOUND` (unknown id), `UNIMPLEMENTED` (Arabic script). The message says
  what was wrong.

## Data

PostgreSQL database `likho_language`, migrations in `src/likho_language/migrations`:
`glossary_terms` (with `heard`, `last_heard_at`), `spellings` (with `applied`, `last_applied_at`),
`spelling_examples`, `language_policies`, `vocabulary_versions`.

The default language policy, used until a workspace defines its own rules: English with
probability 0.80 or more is decoded as English and not transliterated; everything else,
including Urdu, is decoded as Hindi and transliterated.

## Work on it

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest                                  # 93 tests
uv run pytest -m "not integration"             # 67 tests that need no stack
```

The integration tests start the real service on free ports and call it over gRPC against
the stack's PostgreSQL and NATS. Without the stack they are skipped; with
`LIKHO_REQUIRE_STACK=1` (set in CI) they fail instead. Each test uses a workspace of its own
and removes it afterwards.

## Not here yet

* Calls to edit the language policy (the table exists; rules are read from it).
* Tracing.
* The contracts package has no `py.typed` marker yet, so its messages are untyped for mypy.
