# Architecture

AncestryLLM uses a ports-and-adapters layout under `src/ancestryllm`.

```text
CLI / cmd2 console         future API / WebUI adapter
          |                          |
          +---- application services+
                       |
 domain DTOs -- consent policy -- provider contracts
       |               |                 |
 SQLCipher repos   RootsMagic RO      LLM adapters
                   GEDCOM engine
```

Console command sets contain no business logic. `GedcomService`,
`RootsMagicService`, `PromptService`, `ResearchService`, and `LLMService` return
serializable data and raise stable coded errors. A future API may call those
services, but must not import `console` or bypass consent and storage factories.

The built-in module registry is explicit. It imports a module only when enabled
in `config.toml`; there is no entry-point or third-party plugin discovery in v1.
Provider adapters are also explicit and never selected because a credential
exists. `none` is a real offline provider.

The writable application database is SQLCipher-backed and isolated from
RootsMagic databases. RootsMagic files are opened with SQLite `mode=ro`,
`query_only`, an authorizer, inspected schema allowlists, bounded output, a
progress timeout, and before/after hashes. GEDCOM output uses atomic replacement.

Schema changes are represented by packaged Alembic-compatible migrations. The
initial schema stores workspaces, people, identifiers, facts, relationships,
immutable prompt revisions, provider/consent profiles, and privacy-minimal LLM
run metadata. Payload retention is opt-in and remains encrypted.

API routes, browser authentication, multi-user authorization, embeddings,
autonomous agents, third-party plugins, and LLM tool execution are intentionally
outside this release.
