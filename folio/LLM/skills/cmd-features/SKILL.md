---
name: cmd-features
description: Folio vault command /cmd-features [add|remove feature…]. Use when the user runs it, or asks which features the vault has, or wants a command or folder of another feature (journal, weekly report, test queue, phases, diagrams, screens, Spec/Impl/Reference): lists, adds or removes features with Tools/setup/folio.py.
---
# /cmd-features [add|remove feature…]

Vault root: this skill's folder resolved with `realpath`, three levels up; run the commands below from there.

- **No arguments**: run `python3 Tools/setup/folio.py list` and show it: which features are in, what each adds, its commands. If the user's recent work suggests one is missing or unused, say so in one line each.
- **Add**: `python3 Tools/setup/folio.py add <feature…> --dry-run` first and show the files and settings it adds. On a yes, run it without `--dry-run`. It copies from the template at `folio` in `LLM/vault_config.json`; if that path is missing, ask where the template is (or `git clone https://github.com/zelvet-velvet/folio` next to the vault) and pass `--from <path>`. Existing files are never overwritten. Then `python3 Tools/setup/folio.py link`, and for a new plugin without code `python3 Tools/setup/install_plugins.py`.
- **Remove**: `python3 Tools/setup/folio.py remove <feature…>` (it only shows what would go). Point out notes in that list that hold the user's own content; they stay in git history. Delete only on an explicit yes: rerun with `--yes`, then `python3 Tools/setup/folio.py link` (drops the skill links in the code repos).
- **Commit** after a change, only the files the script listed: `chore(vault): add <feature>` / `chore(vault): remove <feature>`.
- The template itself (`"template": true`) has every feature: there, say so and point to `/cmd-new-vault`.
- After adding or removing a plugin, tell the user to reload Obsidian.
