# Reusable development prompts

Keep stable procedures in skills and run-specific inputs in prompt templates.
Both are ordinary version-controlled Markdown that can be read, reviewed, and
copied across tools.

## Layout

```text
AGENTS.md                                    repository instructions and discovery
.agents/skills/milestone-delivery/SKILL.md     reusable orchestration procedure
prompts/README.md                            usage and storage conventions
prompts/next-milestone.md                    per-run goal template
```

## Start a milestone run

1. Read the repository instructions and [milestone prompt](next-milestone.md).
2. Fill in the target, acceptance criteria, completion state, constraints, and
   authorized external actions. The template explicitly authorizes GitHub decision
   summaries by default; remove that permission for a local-only run. Use issue
   links where possible. Supply a budget only if you want one.
3. Paste the completed prompt into the agent. Use Goal mode when available. If the
   host does not discover repository skills, explicitly ask it to read
   `.agents/skills/milestone-delivery/SKILL.md` from the repository root.
4. The main agent orchestrates the run, routes bounded subagent tasks using the
   current supported model/effort catalog and applicable routing policy, verifies
   integration, posts authorized issue decisions, and reports the delivery state.

For example, a run can target an existing milestone with completion set to
“merged changes” and explicitly authorize commit, push, PR creation, and merge.
A release or deployment requires the corresponding authorization and evidence.
Architecture changes within the goal receive security, cost, performance, and
maintainability review before adoption.

The [skill](../.agents/skills/milestone-delivery/SKILL.md) defines the repeatable
procedure. The template supplies each run's scope; `AGENTS.md` remains the source
for repository-wide instructions. Installing these files does not start a run,
change permissions, create an automation, or grant access to another service.

## Portability and routing

The skill uses the [Agent Skills format](https://agentskills.io/specification).
Skill discovery paths, native goal support, subagent tools, model catalogs, and
reasoning-effort controls differ by host. Plain Markdown can always be supplied
as task context, but automatic execution or discovery is not universal.

Use repository or installed `subagent-model-router`, `agent-workboard`, and
GitHub workflow skills when applicable. They retain their own requirements;
this library does not copy local policy or pin models, prices, or private paths.
Where no routing policy exists, the skill supplies task-based selection criteria.
If the host cannot delegate or satisfy a required routing rule, the main agent
keeps that work local and reports the limitation. Any optional tool-specific
adapter should point to or be generated from the canonical skill.

## Safe storage

Commit reusable instructions and synthetic placeholders only. Keep completed
run prompts, credentials, personal data, local paths, logs, workboard databases,
and unsanitized run reports out of Git. Store runtime checkpoints in the host's
approved private or untracked location. Review exact diffs and outgoing commits
with the project's secret checks before publishing. Public issue summaries and
final reports must contain only information suitable for their audience.
