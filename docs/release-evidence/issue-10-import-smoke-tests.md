# Issue #10: GEDCOM importer smoke-test evidence

This page is the release-evidence record for manual imports into Ancestry,
Geni, and MyHeritage. It is intentionally kept separate from automated test
results: passing the local parser/export tests does not prove that a vendor's
current importer accepts or preserves the output.

## Evidence rules

- Use only the fictional fixtures under `tests/fixtures/` or outputs generated
  from those fixtures. Never upload real family-tree records or credentials.
- Record the product name, product/version information visible at test time,
  test date in `YYYY-MM-DD` format, fixture/output identifier, and operator
  notes for every attempt.
- Test the supported GEDCOM 5.5.5 output and the advertised 5.5.1 fallback
  separately when that fallback is generated.
- Record results as `verified`, `failed`, `unavailable`, or `unverified`.
  `unverified` and `unavailable` cannot support a release interoperability
  claim.
- Screenshots, if used, must contain fictional data only and must be stored in
  the release evidence system rather than this repository.

## Test fixture contract

The fixture must include at least:

- a selectable focus/root person and one connected family;
- names, partial and full dates, citations, and a custom/vendor tag;
- an explicitly classified living-person record;
- an export loss report captured alongside each generated GEDCOM file.

The exact generated filename and SHA-256 should be recorded in the release
evidence system, not committed here.

## Vendor results

### Ancestry

| Field | GEDCOM 5.5.5 | GEDCOM 5.5.1 fallback |
|---|---|---|
| Status | `unverified` | `unverified` |
| Product/version | Record during import | Record during import |
| Test date | `YYYY-MM-DD` | `YYYY-MM-DD` |
| Fixture/output | Record fictional fixture and output ID | Record fictional fixture and output ID |
| Root/focus behavior | Record selected person and observed result | Record selected person and observed result |
| Person/family counts | Record expected vs imported | Record expected vs imported |
| Names/dates/citations | Record preserved, transformed, or lost fields | Record preserved, transformed, or lost fields |
| Living-person handling | Record observed behavior without real data | Record observed behavior without real data |
| Custom tags/loss report | Link sanitized evidence and loss report | Link sanitized evidence and loss report |
| Notes/limitations | Complete after import | Complete after import |

### Geni

| Field | GEDCOM 5.5.5 | GEDCOM 5.5.1 fallback |
|---|---|---|
| Status | `unverified` | `unverified` |
| Product/version | Record during import | Record during import |
| Test date | `YYYY-MM-DD` | `YYYY-MM-DD` |
| Fixture/output | Record fictional fixture and output ID | Record fictional fixture and output ID |
| Root/focus behavior | Record selected profile/person and observed result | Record selected profile/person and observed result |
| Person/family counts | Record expected vs imported | Record expected vs imported |
| Names/dates/citations | Record preserved, transformed, or lost fields | Record preserved, transformed, or lost fields |
| Living-person handling | Record observed behavior without real data | Record observed behavior without real data |
| Custom tags/loss report | Link sanitized evidence and loss report | Link sanitized evidence and loss report |
| Notes/limitations | Complete after import | Complete after import |

### MyHeritage

| Field | GEDCOM 5.5.5 | GEDCOM 5.5.1 fallback |
|---|---|---|
| Status | `unverified` | `unverified` |
| Product/version | Record during import | Record during import |
| Test date | `YYYY-MM-DD` | `YYYY-MM-DD` |
| Fixture/output | Record fictional fixture and output ID | Record fictional fixture and output ID |
| Root/focus behavior | Record selected person and observed result | Record selected person and observed result |
| Person/family counts | Record expected vs imported | Record expected vs imported |
| Names/dates/citations | Record preserved, transformed, or lost fields | Record preserved, transformed, or lost fields |
| Living-person handling | Record observed behavior without real data | Record observed behavior without real data |
| Custom tags/loss report | Link sanitized evidence and loss report | Link sanitized evidence and loss report |
| Notes/limitations | Complete after import | Complete after import |

## Release decision

This template does not constitute interoperability evidence. The release
owner must replace every pending status with a dated result or document why the
vendor/version/fallback is unavailable. Release notes must distinguish local
automated guarantees from vendor behavior that remains unverified.
