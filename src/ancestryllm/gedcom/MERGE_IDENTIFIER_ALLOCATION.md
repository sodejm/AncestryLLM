# Merge identifier allocation

`parser.load_sources()` assigns a unique GEDCOM cross-reference identifier to
every merged record and to every exact undefined cross-reference that it must
preserve. The allocator is private to the merge parser; it does not change the
GEDCOM model, serialized service contracts, or public APIs.

## Deterministic allocation

Allocation follows the input source order. Records retain their order within a
source, and undefined cross-references are allocated in sorted order. An
identifier is retained when it has not appeared earlier in that sequence.

For a collision, a simple pointer such as `@I12@` keeps its alphabetic or
underscore prefix and receives the one-based source number plus a suffix:
`@I2_1@`, `@I2_2@`, and so on. Identifiers outside that simple form use the
fallback prefix `X`. The namespace cursor is keyed by prefix and source number,
so overlapping inputs such as an existing `@I2_1@` and a later duplicate
`@I1@` remain unique without changing allocation order. The pointer map then
rewrites record references to the allocated identifiers.

The allocator holds all published identifiers in one set. It also remembers
the next untested suffix for each collision namespace. Once a candidate is
found occupied, that namespace never tests it again. With average constant-time
set membership, the observable allocation work is proportional to allocations
plus occupied candidates encountered, `O(A + C)`. Dense occupied suffix ranges
therefore add one membership probe per occupied candidate instead of rescanning
the range for each new collision. The operation-count tests exercise increasing
dense ranges and adversarial generated-looking identifiers.

## Cooperative cancellation

Dense suffix searches call the supplied cancellation checkpoint when the search
starts and after every 64 occupied candidate probes. A cancellation request
that becomes visible immediately after one checkpoint is therefore observed
within at most 64 further occupied probes. The allocator also checkpoints
immediately before adding either an unchanged identifier or a generated
candidate to the used set. A pending cancellation cannot publish that candidate.

This is cooperative rather than preemptive cancellation. A checkpoint callback
must return for allocation to continue, and Python set membership is treated as
one indivisible probe. The 64-probe budget bounds allocator responsiveness; the
caller retains responsibility for checking cancellation around parsing,
reference rewriting, and `ParsedSource` publication.

## Validation and limits

`tests/test_gedcom_pointer_allocator.py` covers deterministic uniqueness,
increasing operation counts, overlapping identifiers, cancellation before
publication, and reference resolution through `load_sources()`. The broader
GEDCOM merge tests protect existing merge behavior.

Allocator state lasts for one `load_sources()` call and is intentionally
single-threaded. The source number depends on the caller's input order. The
change does not alter data-flow boundaries, provider selection, network access,
immutable input handling, or serialized output contracts, so it does not
require an architecture or threat-model update.
