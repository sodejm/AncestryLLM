# GEDCOM Merge Tool Handoff

Paused on 2026-07-15 on branch `copilot/create-gedcom-merge-tool`.

## Objective

Finish a production-grade GEDCOM 5.5.5 merge tool that preserves source data,
supports rooted exports, and uses conservative deterministic scoring plus
optional Ollama, OpenAI, Gemini, or OpenRouter adjudication. Remote providers
must perform the configured no-person-data credit preflight before a decision
prompt is sent.

## Implemented in the paused change

- Expanded identity evidence beyond birth/death to primary and alternate names,
  all birth/death alternatives, countries, sex, occupation, residence, family
  events, partners, parents, children, `PEDI` roles, and other standard GEDCOM
  facts.
- Missing fields are unknown and omitted from the score denominator. Extra
  facts in a richer source do not penalize that record.
- Added structured `GenealogicalFact`, `RelativeIdentity`, and
  `MatchAssessment` models plus relationship enrichment from `FAM` records.
- Added conservative country inference. State/province-only places are not
  treated as countries, aliases are normalized, and contradictory alternative
  countries block deterministic merging.
- Added one-to-one relative/fact collection matching so one child or spouse
  cannot satisfy multiple source relatives.
- Family event tags must match: for example, `MARR` cannot score as `DIV`.
- Deterministic merging requires strong multi-field evidence and no conflict.
  Names plus family members alone require AI/manual review; a person-level
  anchor, or a matching family event plus two relative categories, can support
  a deterministic sparse-person match.
- Existing duplicate clusters receive a pairwise source-member conflict audit
  before another person can join, preventing transitive bridge merges.
- Merges union facts, alternate names, typed family references, family events,
  partners, parents, children, raw blocks, and custom fields. Richer compatible
  place summaries win without deleting either source block.
- Synthetic serialization now emits alternate names, all structured individual
  facts, and typed `FAMS`/`FAMC` references. The normal CLI continues to use
  preserved source records as the output authority.
- Candidate blocking now includes alternate names, vital events, places,
  countries, and relatives while avoiding a global anonymous-person bucket.
- Updated `tools/README.md` using the cited MIT Communication Lab and Google
  documentation guidance. It documents scoring, privacy boundaries, API keys,
  credit policy, rooted exports, direct providers, OpenRouter routing, and the
  limits of the 5.5.1 header/version fallback and structural validation.

## Verification completed

- `100 passed`:
  `python -m pytest -q tests/test_gedcom_merge.py`
- `python3 -m py_compile tools/gedcom_merge.py tests/test_gedcom_merge.py`
  passed.
- `git diff --check` passed.
- No Python/test lines exceed 88 characters.
- Focused module coverage is 74%; untested lines are concentrated in remote
  SDK/network branches, compatibility branches, and CLI error paths.
- End-to-end offline fixture merge reduced six cross-file people to three and
  retained both `FAM` records. Both families point to the same surviving wife,
  husband, and child, and both marriage-place alternatives remain in output.
- A full repository test collection was attempted earlier with a temporary
  runner but stopped because unrelated `tests/test_router.py` requires
  `langchain_community`, which was not installed in that temporary environment.

## Independent reviewer status at pause

- `Mill` (`019f6899-fe89-76e3-a7fe-790d900310ff`) completed the initial
  documentation audit. Its privacy-identifier wording, 5.5.1 overclaim,
  structural-validation wording, remote-error wording, volatile billing claim,
  and public-contract concerns were addressed or narrowed. A final
  “remaining material issues only” re-review was requested and was still
  running when this handoff was written.
- `Kant` (`019f6899-feec-7621-8201-80b02f07c428`) completed the initial scoring
  and data-loss audit. Its sparse-family auto-merge, family-event tag,
  state-as-country, alternative-country, one-to-one collection, richer-relative,
  richer-place, transitive-cluster, anonymous-blocking, and synthetic-output
  findings were addressed with code and regression tests. A final P0/P1
  re-review was requested and was still running when this handoff was written.
- Both agents should be closed after this branch is pushed. A resumed run should
  perform a fresh short review rather than relying on an unfinished response.

## Recommended next steps

1. Read this file, `tools/README.md`, and the current diff/commit before editing.
2. Run a fresh P0/P1 correctness review and a documentation/privacy review.
3. Address only concrete remaining findings; preserve all unrelated worktree
   changes.
4. Install the repository requirements in an isolated environment and run the
   full test suite, then rerun the 100 focused GEDCOM tests.
5. Consider adding mocked coverage for direct OpenAI, Gemini, and OpenRouter SDK
   request/response paths. Do not make real provider calls in tests.
6. Repeat the end-to-end family fixture check and inspect `FAMS`, `FAMC`,
   `HUSB`, `WIFE`, `CHIL`, `PEDI`, and alternative event blocks.
7. Update this handoff or remove it once the final review is complete.

## Resume prompt

> Resume the GEDCOM merge work on branch
> `copilot/create-gedcom-merge-tool`. Start by reading
> `tools/RESUME_NOTES.md`, `tools/README.md`, `tools/gedcom_merge.py`, and
> `tests/test_gedcom_merge.py`, then inspect the latest commit and worktree.
> Run fresh independent P0/P1 scoring/data-loss and documentation/privacy
> reviews, address concrete findings, install the repository requirements in an
> isolated environment, and run the full suite plus the focused GEDCOM suite.
> Preserve the conservative no-data-loss behavior, remote credit preflights,
> GEDCOM 5.5.5 structural output, root-person export, and support for Ollama,
> direct OpenAI, direct Gemini, and OpenRouter Auto Router. Do not make real
> remote AI calls during tests.
