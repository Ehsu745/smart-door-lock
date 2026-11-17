---
name: cmd-phase
description: Worklog vault command /cmd-phase [phase]. Use when the user says a phase (or a step like 4b) of Quest/Plan.md passed on its target (the phone, a board, staging), or says "next phase": records it in the plan, DEV_STATUS and the Test Queue, commits and pushes, and only then starts the next phase.
---
# /cmd-phase [phase]

Vault root: this skill's folder resolved with `realpath`, three levels up. For projects built in planned phases whose real test is on a target other than the computer the agent works on (an app checked on the phone, firmware on a board, a service on staging). **These note edits are the first tool calls**: no code reading or building before the commit and push, so a passed phase is on record before new work can disturb it.

The phase: the argument, else the one the user named, else the current phase of `Quest/Plan.md`. Ask only if the user named none and the plan has several in progress.

1. **Test Queue** (`Quest/Test Queue.md`, skill `test-queue`; skip this step when the vault has no test queue): tick what the user reported for that phase's group (a `> ` line with what they said); when every box is `[x]` or `[-]`, its status says passed and it moves to the archive as the skill says.
2. **Plan** (`Quest/Plan.md`, read only `## Current phase`): add `✅ passed on <target> <date>` to the phase or step. When a whole phase passed, move its entry to the top of `## Passed` and open the next one under `## Current phase`: `### Phase N: <name> (<date>)`, a `[!NOTE] Status: in progress` callout with its scope and what "passed" means on the target, in 2–4 lines. No plan note yet: create it from the layout in step 2's headings and tell the user.
3. **DEV_STATUS**: tick the phase `[x]` in Ongoing, add the next phase if missing, a Today line `- [x] **Phase N passed** on <target>`; run `python3 Tools/status/counts.py`.
4. **Commit + push** these notes only: `docs(vault): phase <n> passed, <next> started` (skill `git-workflow`, co-author from `LLM/vault_config.json`).
5. Then start the next phase. With a test queue, its target checks become a new group when its first piece is built.
