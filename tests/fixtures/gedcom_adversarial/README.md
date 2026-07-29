# Adversarial GEDCOM fixtures

Every person, place, relationship, and identifier in this directory is
fictional. The fixtures exercise parser and loss-minimizing serializer behavior;
they are not examples of real genealogy data and must never be replaced with
exports from a genealogy product.

`manifest.json` is the authoritative compact catalog. Its dispositions mean:

- `accepted`: the pure GEDCOM engine accepts the input at the cataloged stage.
- `accepted-with-findings`: the syntax is preserved, while a later quality pass
  should report the cataloged semantic anomaly without silently repairing it.
- `rejected`: the pure parser or 5.5.5 output validator rejects the input.

The committed `.ged` files are UTF-8 text with LF newlines:

- `minimal-555.ged` and `minimal-551.ged` characterize supported version input.
- `preserve-extensions.ged` contains Unicode, alternatives, continuations,
  custom nested structures, empty optional values, and advisory-only anomalies.
- `xref-source-a.ged` and `xref-source-b.ged` form a collision and dangling-xref
  pair.

Encoding, newline, invalid-byte, and malformed-line cases are deterministic
factories in `tests/test_gedcom_adversarial.py`. Keeping those byte sequences out
of Git avoids newline conversion, BOM handling, editor repair, and text-diff
ambiguity. The factories write only beneath pytest's temporary directory.

This corpus deliberately stops at the pure parser/xref/wrapping/validator
boundary. NUL policy, file-type, path, size, race, service, CLI, console, and
artifact handling belong to issue #76. Finding emission and end-to-end ingress
coverage are the post-#76 portion of issue #77.
