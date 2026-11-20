---
up: "[[LLM]]"
sticker: lucide//book-open
---
## ℹ️ Project Information
- **Project**: smart-door-lock: 人臉 + 手勢雙重驗證智慧門鎖（Raspberry Pi 5 + Arduino UNO），Flask 手機控制頁、LINE 通知、對講機
- **Repos**: `` at `..`
- **Build / test**: Python 3.11 venv（`uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt`）；`python -m pytest -q`（216 個，Mac 212 passed / 4 skipped）；`python app.py run --sim`；手機版面 `python tools/preview_web.py`；改前端用 Playwright 看 375×548、390×664。目標裝置是 Pi 5（`~/door_pi_gesture`）。完整說明：repo 根目錄 `CLAUDE.md` §3、§6
- **Private**: 私人資訊只在 repo 根目錄 `CLAUDE.local.md`（不進 git）；這個 vault 在 public repo 裡，見 [[Repo 與私人資訊]]

---

## 🦐 Instruction
> [!NOTE] LLM Operational Guide
> - **Core Rule**: Shared rules are in this note; each vault command is a skill (table in [[AGENTS]]), loaded only when it runs. Progress is in `DEV_STATUS.md`. Chat in `chat_language` (`LLM/vault_config.json`; empty: the language the user writes in), short; notes keep the language each command sets.
> - **Read before edit**: Read a vault note right before editing it. The user edits in Obsidian and may have changed or rolled back content; never restore rolled-back content.
> - **Read only what you need**: Large notes (test queues, archives, `Spec/` notes): grep first, then read the matching section.
> - **Features**: The vault has only the features in `features` (`LLM/vault_config.json`); a command or folder of another feature does not exist here. Offer `/cmd-features` when the user asks for one.

### 🛠️ Commands (agent skills)
To change a command's rules, edit its skill file `LLM/skills/<name>/SKILL.md`.

- **Status files**: `DEV_STATUS.md` = Today, Yesterday, Ongoing. Older days: the journal day notes (feature `journal`) or the vault's git history; finished Ongoing items: `PROGRESS_ARCHIVE.md` (feature `archive`; read only when asked or for a report).
- **Trust order** (when sources disagree): code, then today's chat and `DEV_STATUS.md`, then Spec notes, then other vault notes. If a note contradicts the code, say so in the answer, do not repeat the stale fact, and offer to fix the note (`/cmd-notes-sync` when the vault has it).
- **Feature rules** (journal tone, report layout, test groups, diagrams) live in the skills of their feature, not here.

### 🔔 Notifications
- At the very end, one status line per modified section only (e.g., `**[Today's Progress]** Updated in DEV_STATUS.md`). Blank line before/after.

### 🗂️ Vault Note Format (callouts)
- **No H1 title**: make.md shows the file name as the note title, so a note never starts with `# Title`. Name the file well; the first heading in a note is `##`.
- **Diagrams**: a flow, protocol, state machine, frame layout or timeline is clearer as a Mermaid diagram; keep it small, never hard-code colours, and follow the `mermaid-diagrams` skill when the vault has it.
- **Callout before table**: Items that each carry a status or a short detail (tasks, findings, open points, per-area summaries) go in callouts, one per group. A table is only for a real grid read across columns (command / key type / register tables, colour palettes).
- **Callout type = the block's state**: A block with a state shows it in its callout type, and the type changes in the same edit as its boxes or status line. The states follow the checkbox symbols of [[Checkbox Style]], so a box and its block use the same words:

| Checkbox (Checkbox Style) | Item state | Callout type |
| :--- | :--- | :--- |
| `[ ]` To-do · `[/]` Incomplete · `[>]` in progress | open or in progress | `[!todo]` |
| `[x]` Done | finished | `[!done]` |
| `[!]` Important | blocked or waiting for someone (name who or what) | `[!warning]` |
| `[?]` Question | waiting for a decision | `[!question]` |
| `[-]` Canceled | failed or dropped (say why) | `[!failure]` |
| `[i]` Information, or no boxes | no state | `note`, `info`, `abstract`, `example`, `quote`, `tip` |

- **Block from its boxes**: every box `[x]` (or `[x]` and `[-]`) → `[!done]`; every box `[-]` → `[!failure]`; otherwise any `[!]` → `[!warning]`, else any `[?]` → `[!question]`, else `[!todo]`.
  Keep the fold sign (`-` / `+`) and the title.

### 🛡️ Instruction Protection
- Do not modify, truncate or delete the `## 🦐 Instruction` section in `LLM_GUIDE.md` or the command skill files unless the user asks for that change.
