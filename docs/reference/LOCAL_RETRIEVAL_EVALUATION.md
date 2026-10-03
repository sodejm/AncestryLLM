# Local-first retrieval evaluation

Embeddings, vector stores, and retrieval-augmented generation remain
unimplemented in AncestryLLM. This document records the safe design boundary
for a possible future feature; it is not an implementation or a permission to
send genealogy data to a hosted embedding service.

## Proposed local-only baseline

Any first implementation must index only explicitly selected local research
records and store the index, embeddings, metadata, and deletion markers inside
the existing SQLCipher workspace. RootsMagic files and source GEDCOM files
remain immutable inputs. Every materialized record must retain a source
fingerprint, source path or record identifier, normalization version, and
provenance references so a result can be traced back to evidence.

Indexing must be deterministic for the same source fingerprint, model/version,
normalization policy, and embedding configuration. Source changes, model
changes, prompt/template changes, and normalization changes invalidate the
affected materialization rather than silently mixing generations.

## Privacy and consent boundary

- Local indexing is the default and must not contact a network endpoint.
- Living-person and possibly-living data are excluded unless a future explicit
  consent capability authorizes both indexing and retrieval.
- Cloud embeddings require a separately scoped consent grant covering the data
  classes, purpose, provider, model, endpoint, retention, and revocation path.
- Revocation or narrowing of consent must prevent future retrieval and purge
  affected remote or local materializations where policy requires it.
- Diagnostics and benchmark output may contain counts, model identifiers, and
  hashes, but never notes, prompts, embedding vectors, or retrieved text.

## Retrieval and poisoning controls

Retrieved text is untrusted context, not genealogical authority. Results must
carry source and provenance identifiers, remain bounded in size, and be
separated from system instructions. The application must detect and report
stale, deleted, malformed, or conflicting source material rather than allowing
retrieval to overwrite authoritative records.

The design must address:

- malicious or misleading text inserted into an indexed note;
- cross-person or cross-workspace index leakage;
- stale vectors after source edits or consent changes;
- embedding/model supply-chain changes;
- denial-of-service through oversized documents or unbounded nearest-neighbor
  requests; and
- accidental inclusion of restricted or living-person data.

## Explicit non-goals

This evaluation does not authorize autonomous agents, tool calls, generated
shell/Python execution, generated write-capable SQL, destructive family-tree
writeback, model training, or treating model output as a source of truth.
Deterministic records, citations, and human review remain authoritative.

## Future acceptance gates

Before implementation, the project needs a versioned storage design, explicit
consent capability, deletion and retention semantics, source-fingerprint
invalidation tests, restricted-data tests, poisoning/ provenance tests, and a
network-offline test. A future cloud provider must also document endpoint
allowlisting, retention, regional handling, and key/credential management.
