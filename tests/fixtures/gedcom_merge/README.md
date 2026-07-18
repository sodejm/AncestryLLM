# GEDCOM merge quality fixtures

All names, places, repositories, and events in these fixtures are fictional.

Use `Maren Hollow` (source pointers `@A_ROOT@` and `@B_ROOT@`) as the exact
root name. After deterministic cross-source merges, its connected component is
intended to contain nine people: two parents, Maren, two spouses, an adopted
child, a sibling, the sibling's spouse, and one cousin. The source family
records remain separate, so the rooted output retains four families from each
quality source.

The two fixture files, `quality-source-a.ged` and `quality-source-b.ged`, are
line-parseable by `tools/gedcom_merge.py`. Together they intentionally
exercise:

- sparse `@A_ROOT@` versus rich `@B_ROOT@`, including missing `SEX` on the
  source-A person used as `WIFE`, missing dates/places, aliases, two spouses,
  an adopted child with `PEDI adopted`, and a cousin branch;
- exact, high-confidence same-source duplicates `@A_DUP_ONE@` and
  `@A_DUP_TWO@`, which cross-file merge candidate selection intentionally does
  not resolve;
- invalid `31 FEB 1965` on `@A_BAD_DATE@` and incompatible birth alternatives
  on `@A_CONFLICT@`;
- a married name preceding a maiden name on `@A_NAME_BAD@`, plus correctly
  ordered typed names on `@A_NAME_GOOD@` and `@B_NAME_NEGATIVE@`, an only-
  married-name record, and an untyped/alias-only record as negative controls;
- dangling `@AF_404@` and `@A_GHOST@` references, person-to-family and
  family-to-person nonreciprocal links, uncited events beside properly cited
  controls, and underscore-prefixed custom tags;
- source A's missing `CHAR` and trailer, and source B's duplicate/nonterminal
  trailers. These are source-quality defects, not line-grammar failures.

`malformed-rejected.ged` must fail deterministically at line 8 with an invalid
GEDCOM level error for `1NAME Missing required whitespace after the level`.
