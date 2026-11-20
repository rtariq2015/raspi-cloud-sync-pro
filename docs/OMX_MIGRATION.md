# OMX migration — 19 November 2026

## Scope and installation

Owner authorization OMX-MIGRATION-20261119 covers reversible laptop-local migration, disposable tests, activation after acceptance, exact reviewed migration commits/push and one draft PR to development. It grants no Pi/scanner, storage/image, cloud, credentials, privileges, paid API, administration, merge or deployment access. The original request is retained privately with its hash in the existing coordinator ledger. Older pending/rejected packages and consumed permissions remain unchanged.

Verified laptop installation: Orca 1.4.262, Codex CLI 0.165.0, OMX 0.21.46, Node 24.22.2, tmux 3.4, Git 2.43.0. Reused all installed components and the existing ChatGPT OAuth account; no installation/update or account changes. Verified origin rtariq2015/raspi-cloud-sync-pro and development base. Original raspi-work and development branches remain unchanged; migration is isolated in rtariq2015/omx-migration.

Project setup used the installed version's supported legacy delivery, project scope, merge policy, MCP none and Team disabled. Preview preceded writes. A second supported setup with --disable-hooks removed OMX-owned hook registrations. No force refresh, plugin-mode duplicate, alternate provider or new scheduler was installed. Generated upstream prompts/agents/skills remain local and reproducible; portable project configuration and instructions are tracked. Existing global instructions, auth and integrations are preserved.

To reproduce generated artifacts with the same installed OMX version, back up the project's current configuration first, then from this repository root use:

```sh
omx setup --scope project --install-mode legacy --merge-agents --mcp none --disable-team --disable-hooks
```

Do not run setup from home. Inspect the diff after setup and retain the serial restrictions in .codex/config.toml; setup must not enable goals/hooks/native agents or overwrite the project overrides. This command is setup, not routine launch. The tested entry pins versions and disables auto-update. No unattended update should replace this tested configuration.

## User entry and boundaries

Open the migration workspace in Orca and use its **Raspi AI Team** terminal entry. The equivalent supported shell entry is `./tools/raspi-ai-team`. Goals go in that terminal's Codex composer; progress, tool output and replies appear there. The wrapper attaches to the single `raspi-ai-team` tmux session and runs `omx launch --direct` with --no-daemon, Sol/medium, workspace-write, on-request approval, agents.enabled=false and legacy multi_agent=false. Apps/plugins/browser/computer/MCP mutation surfaces, hooks, goals and Team are disabled in the launch. CODEX_HOME remains the system ~/.codex; credentials are never copied into Git. The single currently configured MCP server is disabled only for this workflow.

Seven responsibilities, allowed work and handoffs are defined in [.codex/RASPI-WORKFLOW.md](../.codex/RASPI-WORKFLOW.md). The root AGENTS.md preserves upstream generated sections and adds the owner-specific serial override. Native role TOMLs carry explicit agentModels/agentReasoning pins generated from .codex/.omx-config.json, but native spawning is disabled. Reading an executor prompt does not change the leader model or create independent review. Real protected separate sessions use the retained raspi-agent role launcher.

| Route | Effective assignment |
| --- | --- |
| Coordinator/direct development/debugging | gpt-6.1-sol / medium |
| Routine checks/documentation | fixed protected raspi-agent readonly-check/documentation, gpt-6-luna / low |
| Research/architecture/integrity escalation | fixed protected raspi-agent research/architecture, gpt-6-astra / high; only with a concrete diagnosis |
| Independent review | fixed protected raspi-agent testing-medium --verify, gpt-6.1-sol / medium / read-only / never |

The account catalog lists these model IDs and low/medium/high. Actual authenticated Luna and Sol executions are demonstrated; no claim of an Astra execution is made. Never automatically select above high.

The two-specialist requirement uses the explicitly authorized **serial fallback**. Team skill/context generation and native child creation are disabled. The standalone OMX Team CLI has no demonstrated runtime deny switch; it is outside the selected serial route and must not be invoked. Its canary stopped on a dirty-worktree precondition, which is not evidence of a Team-mode deny. One named tmux leader attaches rather than duplicates; run protected reviews serially and never start overlapping Orca couriers/teams. There is no machine-wide session limiter. External manual launches are outside this workflow. A durable automatic retry cap has not been demonstrated; unattended retry loops are disabled. Persist objective ID/counters in the existing ledger, at most two supervised corrections, and stop for reassessment at the limit. No hard monetary cap is claimed.

## Durable state and recovery

Existing docs/ROADMAP.md and docs/STATUS.md remain project truth. ~/.local/state/raspi-setup/COORDINATOR.json routes the current project workflow; COORDINATOR-LEDGER.json retains all authorizations, historical evidence, counters and execution status. Private migration evidence/backups are indexed there. OMX .omx session state and Codex history are runtime evidence, never authority or an alternative roadmap.

For interruption: Escape interrupts the current turn in the tested TUI. Interruption leaves a running shell command alive; use the displayed `/stop` command, then inspect actual process state. The test found that `/stop` did not terminate two sandbox descendants; the canary later completed naturally. Explicit identity-checked termination of owned descendants is required when they remain. No successful cancellation is inferred from that first attempt. Ctrl+C may interrupt/exit depending on TUI state, so do not rely on it to terminate descendants. Ctrl+B then D detaches tmux; relaunching the entry reattaches. /quit exits Codex normally. After a leader exit use the recorded exact Codex session ID with the same protected `./tools/raspi-ai-team --resume <UUID>` entry only after inspecting ledger/artifacts and matching permissions; do not resume an arbitrary latest session or start a second leader. Closing Orca may close its PTY while tmux survives; inspect, do not assume. Sleep pauses progress. Shutdown, unavailable quota or disconnected execution cannot promise continuous work. Do not rotate accounts or buy credits. No background daemon/scheduler was added.

A launcher exiting never proves its worker descendants stopped. Inspect the exact tmux pane PID and owned descendant tree, terminate only proven owned processes if needed, and verify absence afterward. Never kill-server or kill unrelated Codex/Orca sessions. For OMX mode cancellation, inspect `omx cancel --help` and use only the proven exact-session target; do not assume mode cancellation kills processes. No active Team/mode loop is required for this serial entry.

## Rollback and component inventory

Private dated backups include original path/mode/SHA256, global policy/config, WORKFLOWS, coordinator policy/ledger and raspi-agent. The installed OMX package plus dependencies has a matching-version archive and complete file hash manifest. Authentication/transcripts/private machine details are excluded from Git. The rollback rehearsal restored the same OMX version into an isolated mirror, archived a migration-owned file, and refused to overwrite a simulated later user edit.

To roll back: stop and verify the owned new leader/descendants, retain all diagnostic evidence, and disable the migration entry. Leave the migration branch/worktree preserved. Compare each migration-owned file against its activation manifest before restoring the matching baseline; conflicts require a three-way reconciliation, never blind overwrite. Restore only changed coordinator routing keys and the exact appended WORKFLOWS section; preserve new ledger evidence and later owner approvals. Restore the old raspi-agent bytes only if its current hash still matches this migration's version. The original raspi-work checkout, global model configuration and account were never replaced. Re-enable its existing protected route only after confirming no new leader remains. Do not reset the entire ledger or global .codex/.omx.

| Component | Disposition and reason |
| --- | --- |
| Orca, Codex/account, Node, tmux, Git, installed OMX | KEEP; compatible existing installation, versions pinned at launch |
| Project OMX generated catalog | KEEP locally; supplied specialists, reproducible with exact version |
| Project portable policy/model config/entry | UPDATE/ADD; owner-specific scope and serial fallback |
| raspi-agent | RETAIN FOR PROTECTION; technically read-only specialist/reviewer boundary, updated with current agents.enabled=false |
| Existing ledger, roadmap/status, research/test evidence, Trail, Actions, CodeRabbit | KEEP; existing history and integrations |
| Previous default Orca courier coordination for this project | REPLACE only as active dispatch selection after acceptance; old history retained |
| Rejected Revision 3 guard and historical proposals | ARCHIVE by reference; never activated or rebuilt; retained evidence stays in its existing dated location |
| Generated hooks from initial isolated setup | ARCHIVE by supported OMX --disable-hooks; no duplicate hook system active |
| Auth, global .codex, branches, Pi configuration, images/history | RETAIN FOR PROTECTION; untouched |

## Acceptance record

Acceptance is recorded from attributable command exits/artifacts and independent review, not prompt text or green labels. The private ledger evidence contains authenticated execution, six-test fake implementation, independent rerun, read-only write denial errno30, writer handoff, exact-session resume, outside-workspace write denial errno30 and matching-version rollback. The first legacy multi_agent flag did not remove reported spawn tools; agents.enabled=false is the scoped compatibility correction. Failed standalone sandbox CLI probes are retained as failed probes and are not protection evidence.

The corrected leaf launch exposed no spawning tools. Actual Orca launch preserved the system Codex home and explicit Sol/medium/workspace-write/on-request flags. The first cancellation failed: /stop left owned descendants alive and the canary completed naturally. A second bounded recovery test verified exact ancestry/start-time identity, interrupted the turn, sent SIGTERM only to those owned disposable descendants, and confirmed every saved owned PID absent and no completion artifact. This is coordinator-supervised recovery, not a claim that native cancellation kills all workers. Independent final review, activation and latest-head publication checks are recorded only after their actual results are available. Doctor's hook-disabled and legacy-flag warnings are expected configuration exceptions, not evidence of active hooks or unrestricted spawning.

CodeRabbit configuration currently excludes draft PRs (`reviews.auto_review.drafts: false`). Leave the PR draft as authorized and report skipped/unavailable review; do not change repository administration or claim a pass. Local/CI success is not Pi/hardware acceptance.

## Example future goal

“Create an authorization proposal for a laptop-only documentation update explaining Safe Eject and exclusive ownership using existing evidence. Use the current objective ledger; do not access the Pi, scanner, cloud, credentials, images or services, implement application features, or publish anything. Return the exact scope and acceptance criteria for my approval.”

## Primary references

- [OMX 0.21.46 setup](https://github.com/Yeachan-Heo/oh-my-codex/blob/v0.21.46/skills/omx-setup/SKILL.md)
- [OMX 0.21.46 model routing](https://github.com/Yeachan-Heo/oh-my-codex/blob/v0.21.46/docs/reference/omx-config-schema-routing.md)
- [OMX 0.21.46 Team](https://github.com/Yeachan-Heo/oh-my-codex/blob/v0.21.46/skills/team/SKILL.md)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Orca Codex/account routing](https://orca.dev/docs/agents/codex)
- [Orca terminal](https://orca.dev/docs/terminal)


## Observed activation outcome

Activated the exact independently reviewed entry in Orca's **omx-migration** workspace,
terminal **Raspi AI Team**, after the PASS for bounded coordinator-supervised serial use.
The authenticated project smoke read the actual instructions, ran the staged-diff check
(exit 0), and wrote the exact objective, measured cwd and computed configuration hash.
The coordinator independently compared the artifact and hash and re-ran the diff check.
The owned process tree has one Codex model leader in the intended checkout, using the
system Codex home, Sol/medium, workspace-write/on-request and agents.enabled=false;
no native specialist child was launched. The leader is idle awaiting the next authorized
goal. Current routing selects this entry; the old protected launcher and all prior
authorization/research evidence remain retained. No application source was changed.

The initial standalone cancellation failure remains an explicit limitation. Only bounded
coordinator-supervised serial use is accepted; native cancellation and unattended retries
are not advertised as reliable. The matching-version rollback mirror and conflict-preserving
restoration checks passed. Draft publication and head-specific CI/review results are
recorded in the PR and private execution ledger once observed.
