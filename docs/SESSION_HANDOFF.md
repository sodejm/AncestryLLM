# Handing a session to an implementer

Save one JSON file per request under `docs/session-plans/`. Use a stable descriptive name and update the same file when implementation starts. The [handoff for these hooks](session-plans/behavior-handoff-gates.json) is a populated example.

## Before handing off a plan

1. Set `stage` to `plan`. In `request`, state the user's observable outcome. In `scope`, name the entry points and limits. List the working directory, dependencies and preparation commands in `setup`. Put compatibility requirements and decisions the implementer must preserve in `constraints`.
2. Write `steps` in execution order. Give each an `action` with the change to make, `paths` with the exact files to change, and a `verify` object with a runnable `command` and its `expected` result. Include the current behavior and where to start reading. Resolve decisions a fast implementation model would otherwise have to guess.
3. Give every requested outcome an `acceptance` case with an `entrypoint`, concrete `given` input, `when` action and observable `then` result. Include distinct `success` and `boundary` cases. Name the implementation and test files in `paths` and identify the exact test in `test`. Use `runner` of `unittest` for `file.py::Class::test_method`, or `pytest` for `file.py::test_function` or a class method. Planned files and tests can be created later.
4. List unresolved decisions, unavailable equipment and outstanding manual acceptance in `pending`. Use an empty array when all are resolved. Run the check below even for a planning session that ends without a commit.

```sh
python3 scripts/session_delivery.py plan docs/session-plans/behavior-handoff-gates.json
```

Use your own handoff filename when starting a new task.

## During implementation

Change `stage` to `implementation`. For each case, run the assertion against the prior behavior, implement the change, and rerun it. Record the command, observed failure and passing result in the session handoff or delivery notes. Exercise the same public entry point a caller uses. For desktop behavior, the Python case can drive the actual CLI or native test runner and assert a specific result. Record any native OS or accessibility qualification separately.

## Git checks

Install both hooks with `make hooks`.

- **Commit** reads the Git index, including partial commits. It validates changed handoffs and checks that implementation cases cover changed code, tests and configuration since the branch baseline. Review the staged result with `python3 scripts/session_delivery.py quick`. Planning-only handoffs can name future work.
- **Push** checks implementation scope against changed files and runs every distinct named case in the committed snapshot. A missing, empty, failing, skipped or expected-failure case blocks the push. Outstanding implementation acceptance also blocks it. Resolve it, or agree on a smaller scope with the requester and update the handoff. Existing repository checks still apply.
- **Review** the assertions against the original request. A passing test establishes only what it observes. Local hooks run in clones where they are installed. Git cannot inspect a chat-only plan or attest to a live or manual outcome.
