## Summary

Describe what this PR changes and why.

## Acceptance criteria

Link the issue and map each acceptance criterion to its exact test or other
verification evidence.

| Acceptance criterion | Test or evidence |
| --- | --- |
|  |  |

## TDD evidence

Complete the red-green-refactor record for every behavioral change. Include
exact test names so reviewers can reproduce the focused loop. If this change is
genuinely non-behavioral, replace the three subsections with:
`No behavioral test applies because ...`, then describe the focused validation.

### Red

- Exact test names and files:
- Command:
- Expected failure observed before production changes:

### Green

- Minimum implementation that made the focused test pass:
- Command and result:

### Refactor

- Cleanup performed while the focused test stayed green:
- Command and result:

## Validation evidence

Record exact commands and results. Mark non-applicable gates with a reason.

| Command | Result |
| --- | --- |
| Focused tests |  |
| `make pre-push` |  |
| `make workflow-audit` if workflows changed |  |

## Risk Level

- [ ] Low
- [ ] Medium
- [ ] High

## Safety verification

- [ ] Relevant targeted checks passed during development
- [ ] `make pre-push` passed before the branch was pushed
- [ ] `make workflow-audit` passed if GitHub Actions workflows changed
- [ ] Security-sensitive paths remain read-only (`:ro` for `family_trees`)
- [ ] No secrets or private genealogy data added
- [ ] `provider=none` remains network-free

## Checklist

- [ ] Head branch uses the appropriate `feature/*`, `bugfix/*`, or `hotfix/*`
  prefix and was created from current `origin/main`
- [ ] Every behavioral acceptance criterion maps to an automated test
- [ ] Initial failures were observed for the expected reasons
- [ ] Existing tests and safety controls were not weakened to make the change pass
- [ ] Documentation updated if needed
- [ ] Backward compatibility considered
- [ ] Obsolete code and configuration made unnecessary by this change were removed

## Notes for Reviewers

Anything reviewers should focus on.
