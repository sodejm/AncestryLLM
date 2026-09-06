# Next milestone goal prompt

Read [the usage guide](README.md), replace the bracketed fields, then paste the
prompt below into your agent's Goal mode or ordinary task input. Leave runtime
values in the task or approved private state rather than committing the filled
prompt. The completion state alone does not authorize publishing or deployment.

```text
Use .agents/skills/milestone-delivery/SKILL.md to create and pursue this goal,
or continue the existing matching goal when one is already active:

Target: [named milestone/release, or next milestone from the documented roadmap]
Acceptance criteria: [criteria or authoritative issue/roadmap references]
Required completion state: [validated implementation / merged changes / published release / deployed]
Constraints and non-goals: [compatibility, privacy, scope, or other constraints]
External actions authorized for this run: GitHub issue/PR decision and outcome summaries;
[add any other allowed actions: commit, push, PR creation, merge, release publication,
deployment, or other actions. Remove the summary permission for a local-only run.
Omitted actions gain no new authorization.]
Budget or time limit: [optional explicit limit; otherwise use existing host/user limits]

Act as the main goal orchestrator. Own the plan, architecture decisions,
integration, verification, GitHub updates, and final report. Delegate bounded,
independent tasks when useful. Follow the available subagent-model-router and
coordination policy before each launch; otherwise select supported models and
reasoning efforts from the live catalog using task complexity, risk, cost, and
verification needs. Record each route and its rationale. Verify returned work.

Iterate autonomously until the acceptance criteria and completion state are
verified. Architecture changes within this goal are authorized after scrutiny
for security, cost, performance, and maintainability. Record the alternatives,
tradeoffs, evidence, and migration or rollback plan for significant decisions.

Record concise decision and outcome summaries. Post them to relevant GitHub
issues and the run's PRs only when authorized for this run; otherwise include them
in the final report. Preserve privacy and follow the repository's workflow and
review gates. Continue already-authorized work without repeated confirmation;
ask only for missing input or authority that blocks a consequential step.

Stop at this target or an explicit pause. If blocked or host limits interrupt
the run, preserve a safe checkpoint and report the exact next action. Finish
with the achieved scope, decisions, model/effort routes, validation evidence,
issue/PR links, verified delivery state, and any remaining risks or blockers.
```
