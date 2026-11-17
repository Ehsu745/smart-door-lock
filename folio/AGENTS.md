# Folio vault: agent instructions

This Obsidian vault is the work log and notes of one project, kept with an LLM coding agent. Any coding agent reads this file; Claude Code reads it through `CLAUDE.md`.

## Template or vault
- `"template": true` in `LLM/vault_config.json` means this folder is the **Folio template** itself, shared publicly. Never write project notes, progress or project names into it, and never push project work to its remote. To start a project, run `/cmd-new-vault`: it makes a separate vault with its own git repo.
- Otherwise this is a project vault: `project`, `projects` (the code repos it logs) and `features` in `LLM/vault_config.json` say what it covers.

## Start here
- Shared rules: `LLM/LLM_GUIDE.md` (read it before editing vault notes). Progress: `LLM/DEV_STATUS.md` (read only the part you need).
- Personal values (owner, commit co-author, chat language, projects, features, skill folders): `LLM/vault_config.json`.
- Chat in `chat_language` from `LLM/vault_config.json` (empty: the language the user writes in). Notes, code and commits keep the language their command or rule sets.

## Commands
The user runs a command as a slash command (`/cmd-sync`; typing `/cmd` lists them all). Each one is a skill: agents with skill support load it from their skills folder; any other agent opens the file `LLM/skills/<skill>/SKILL.md` and follows it. The table follows the vault's features (`/cmd-features` changes them).

<!-- folio:commands -->
| Command | Skill | What it does |
| :--- | :--- | :--- |
| `/cmd-sync` | `cmd-sync` | Update Today / Yesterday / Ongoing in `DEV_STATUS.md` from the chat |
| `/cmd-rollover` | `cmd-rollover` | Today → Yesterday (and the journal day note when the vault has one), commit |
| `/cmd-features` | `cmd-features` | List, add or remove this vault's features |
| (branch, commit, merge, MR) | `git-workflow` | Git and merge-request rules for the project repos |
| (test groups, test results) | `test-queue` | `Quest/Test Queue.md` rules |
| `/cmd-phase [phase]` | `cmd-phase` | A phase of `Quest/Plan.md` passed on its target: record it first, then start the next |
| `/cmd-notes-sync [date]` | `cmd-notes-sync` | Fix stale vault notes, move `[x]` Ongoing items to `PROGRESS_ARCHIVE.md` |
| `/cmd-journal [date]` | `cmd-journal` | Chinese journal bullets in the day note, tone learning |
| `/cmd-report [date]` | `cmd-report` | Three Chinese parts in the week note |
| (adding or changing a diagram) | `mermaid-diagrams` | Which Mermaid type to use, readability, theme classes |
<!-- /folio:commands -->

## Setup on a new machine
- `python3 Tools/setup/install_plugins.py` downloads the plugin code and the theme (git keeps only their settings); run it before the first Obsidian start.
- `python3 Tools/setup/folio.py link` writes the vault's command stubs (`.claude/skills/`, `.agents/skills/`: committed, no symlinks, since Obsidian Git on a phone breaks on them) and links every skill into each code repo in `projects`, never the global folders. Each link is listed in that repo's `.git/info/exclude`, so a repo someone else owns gets no new files in its git status. Rerun it after adding a skill, a feature or a project.
- Python 3.9+ for `Tools/` scripts.

## Commits in this vault
Only the files the command names; never `git add -A`. Message `type(scope): summary`, ending with the co-author line of the agent that did the work (`coauthor` in `LLM/vault_config.json` is the default).
