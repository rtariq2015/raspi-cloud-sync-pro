# Raspi serial OMX workflow

RASPI_SETUP_GUARD_V1. These project-specific restrictions override broader generated OMX defaults. They grant no application, hardware, cloud, privileged, merge, deployment or spending authorization.

## Entry, authority and state

Use the single `tools/raspi-ai-team` entry in Orca. It attaches to one named tmux leader, pins the tested local OMX/Codex versions, disables Team/native child agents/hooks/goals and auto-update, and keeps the existing system Codex home/account. Enter goals at the Codex prompt. OMX Team is disabled; do not use Team, Autopilot, Ralph, Ultragoal, unattended retry loops, native children, Orca couriers or other dispatch alongside this entry.

Read existing docs/ROADMAP.md, docs/STATUS.md, relevant acceptance records, ~/.codex/AGENTS.md and ~/.local/state/raspi-setup/COORDINATOR.json and COORDINATOR-LEDGER.json first. The existing ledger is the only authorization/execution record. OMX session state is runtime evidence, never authorization or a second roadmap. Migration OMX-MIGRATION-20261119 authorizes configuration/testing and its draft PR only. Never revive pending/rejected packages or consumed one-shot commands. Revision 3 remains rejected. Future development needs an exact owner-authorized goal and scope.

Keep one durable objective/task ID across retries, resume and renaming. Check prior counters and artifacts before work. Maximum two automatic corrective cycles and two substantially similar failures; record each BEFORE starting it. No supported persistent automatic cap was demonstrated: unattended retries are DISABLED. Perform only one supervised bounded attempt at a time, stop for reassessment at the stored limit. Do not reset counters by starting a new worker or task ID.

## Seven responsibilities and handoffs

All responsibility shifts are serial. The coordinator does simple approved work directly and reads the supplied role prompt from .codex/prompts/<role>.md for the relevant phase; this is a prompt surface, not a claimed independent subagent or a model switch. Native TOML role settings are prepared but native spawning is DISABLED. Do not claim their settings apply to the current leader. The leader remains gpt-6.1-sol/medium. For a real separate model assignment use only the existing protected raspi-agent route below; record actual launch/header evidence.

| Responsibility | Supplied specialist | Input and allowed work | Output / completion / next handoff |
| --- | --- | --- | --- |
| Coordinator | planner / analyst | Authorized goal, ledger, roadmap; classify dependencies, acceptance and risks | Durable bounded plan and one ready phase; continue only approved dependencies |
| Research / architecture | researcher / architect | Targeted records and primary evidence; fixed raspi-agent research or architecture, read-only, Astra/high | Evidence-backed decision/proposal; coordinator applies only already-authorized decisions |
| Development | executor | Approved scope and acceptance, isolated Orca topic worktree; Sol/medium | Minimal diff, actual tests, recovery notes; hand to testing |
| Testing | test-engineer / debugger | Diff, acceptance, fixture commands; Luna/low for simple checks or Sol/medium for debugging | Attributable command exit/output/artifacts; hand to independent review |
| Recovery / safety | architect / verifier | Preservation invariants and rollback evidence; protected read-only route | Risks and exact restoration checks; block unknown integrity/ownership |
| Documentation | writer | Verified test/review results; fixed raspi-agent documentation, Luna/low, read-only | Proposed text; coordinator updates existing progress/acceptance records |
| Independent review | code-reviewer / verifier / critic | Exact diff, tests, activation and rollback; `raspi-agent testing-medium --verify --task-file <private-task>` | Independent read-only PASS/FAIL/BLOCKED/UNVERIFIED with evidence; coordinator verifies and consolidates |

Independent review must be a separate technically read-only session. Reuse ~/.local/bin/raspi-agent (fixed sandbox, no escalation, apps/plugins/MCP/browser/hooks/nested agents disabled). Never use a write-capable native child for review. Documentation and research use their corresponding fixed launcher roles, never inherited Full Access chats. Return proposed edits as text. Do not claim a model from its role label; preserve actual header/argv evidence. Do not add per-role programs or fork upstream code.

Only one named leader may operate this workflow. All work and reviews run serially; do not overlap specialist sessions. Native agents.enabled and legacy multi_agent are false and OMX_TEAM_MODE=disabled is required. The at-most-two requirement uses the owner's serial fallback: there is no supported combined Team/native/Orca global limiter here. Outside manual CLI/native chats are outside this workflow and must not be described as machine-wide enforcement. Never re-enable a parallel route on the strength of pane count. A substantive helper always counts as a specialist.

## Authorized execution sequence

1. Confirm exact approval and objective ID, scope, allowed environment, components, limits, stop conditions and acceptance. Record task role, effective model/effort, status, evidence and next permitted action in the existing ledger. Queue dependencies; no duplicate work.
2. Retrieve bounded Trail context and targeted source. Reuse the existing installation and per-worktree cache; no paid summaries/network/global hooks. Source/doc fallback is permitted when retrieval is stale or unavailable.
3. Implement only the approved scope in an isolated Orca worktree. Preserve unrelated edits. No new framework/dependency/feature without authority.
4. Run meaningful tests and collect attributable exit codes/artifacts. Echoed goal text and launcher exit are never acceptance evidence. Independently inspect expected artifacts and owned worker/process state.
5. Obtain one serial protected independent review. Treat reports/CodeRabbit as evidence to verify. Apply only confirmed scoped findings, within persisted counters. Escalation to Astra/high requires a concrete new diagnosis and ledger reason; never above high automatically. Pause on quota; no account rotation or new billing.
6. After code edits, refresh structural Trail using explicit approved worktree/cache paths, verify freshness and bounded no-refresh retrieval, deduplicate already-authorized privacy-safe learnings before invocation-local TRAIL_HOME=<cache>/.trail-home trail note, update existing ROADMAP/STATUS/acceptance evidence, run relevant tests and Trail consistency, inspect exact staged paths. Never force rejected/duplicate notes. No cache or automatic .trail directory staging.
7. If publication explicitly authorized: ordinary dedicated topic-branch push, one PR to development, collect actual latest-head Actions/CodeRabbit findings; at most two persisted correction/review cycles. No force push, auto-merge, branch deletion, release or deployment. Continue only approved dependency-ready work. Otherwise record publication gated.

## Preservation and excluded operations

Production target is SOMATOM X.cite conventional writable USB Mass Storage, not MTP. Keep Safe Eject/exclusive ownership for capture; no-eject acquisition remains unproven. Preserve reports, image identity, queues/retries, versions/history, Google authorization, independent verification and rollback resources. Older November 15 status describes historical MSC acceptance; November 18 saved observations describe a later MTP-active state. Neither establishes live state today. Do not inspect the Pi to reconcile history during this migration.

No Raspberry Pi/scanner access, live USB/service/network changes, OS reinstall, SSH key replacement/repinning, storage/image/format operations, real cloud/backup operations, credentials/destinations, patient data, hospital connection, elevated privileges, paid API, administration, merging or deployment under this migration. Those require separate exact approval. A worktree is not a sandbox. Never weaken actual launch restrictions to finish a task.

## Interruption and recovery

Use Escape to interrupt a turn; the tested TUI leaves background shell commands running. Use its `/stop` command and inspect owned processes. Do not treat interruption as process termination. /quit exits the leader; detach tmux with Ctrl+B then D to leave it running. Closing Orca can end its PTY while tmux may survive; inspect tmux and owned processes, do not assume either persistence or termination. Laptop sleep pauses progress; powered-off/exited processes do not execute work. On quota stop without switching accounts. Resume only the exact saved Codex session after checking artifacts and ledger counters. Runtime stop/cancel does not prove descendant termination. Check the owned process tree and exact tmux session separately. Never kill unrelated sessions or use kill-server.

See docs/OMX_MIGRATION.md for the tested activation, audit limits and matching-version rollback. No continuous background scheduler is installed.
