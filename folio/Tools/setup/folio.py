"""Folio: make a project vault from this template, change a vault's features, link its skills.

Usage (python3 3.9+, standard library only; from any folder):
    python3 Tools/setup/folio.py list                    # features, and which ones this vault has
    python3 Tools/setup/folio.py new DEST --name NAME [--features test-queue,plan] [--repo PATH]...
                                [--owner NAME] [--coauthor LINE] [--chat-language LANG] [--about TEXT]
                                [--no-git] [--dry-run]
                                                         # from the template only; no --features on a
                                                         # terminal: a numbered menu
    python3 Tools/setup/folio.py add FEATURE... [--from TEMPLATE] [--dry-run]
    python3 Tools/setup/folio.py remove FEATURE... [--yes]   # without --yes: only shows what would go
    python3 Tools/setup/folio.py link [REPO...] [--remove-global] [--dry-run]

Features are defined in Tools/setup/features.json. A new vault is a plain folder with its own fresh git
repo: no remote, nothing points back at the template, so it can never push into it. The template's
LLM/vault_config.json has "template": true; a vault has "features" and "folio" (the template's path,
used by add) instead.

link: the vault keeps its commands as committed stubs (<agent folder>/<name>/SKILL.md: "read
LLM/skills/<name>/SKILL.md"), so a fresh clone, a cloud session or Obsidian Git on a phone (which breaks
on symlinks) has them. Each code repo in "projects" gets one symlink per skill in its own skills folder
(never the global ~/.claude/skills), each listed in that repo's .git/info/exclude, so nothing shows in
its git status: safe in a repo someone else owns. Links to skills the vault no longer has are removed.
Links in code repos are WSL symlinks: an agent running natively on Windows does not follow them.
"""
import argparse, datetime, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]          # the vault (or template) this script sits in
SKIP_DIRS = {'.git', '.makemd', '.trash', '__pycache__'}
CMD_START, CMD_END = '<!-- folio:commands -->', '<!-- /folio:commands -->'
STUB = '---\nname: {n}\ndescription: {d}\n---\nRead `LLM/skills/{n}/SKILL.md` (the real skill, kept in the vault) and follow it exactly.\n'


# ---------- small helpers ----------

def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def save(p, data):
    Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def registry(root):
    p = root / 'Tools' / 'setup' / 'features.json'
    if not p.exists():
        sys.exit(f'{root}: no Tools/setup/features.json (not a Folio template or vault)')
    return load(p)


def config(root):
    return load(root / 'LLM' / 'vault_config.json')


def files_of(root):
    """Every file of the folder that git would keep (tracked + untracked, minus ignored), as posix paths."""
    if (root / '.git').exists():
        out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                             capture_output=True, check=True).stdout.decode('utf-8')
        return sorted({f for f in out.split('\0') if f and (root / f).is_file()})
    found = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        found += [(Path(d) / n).relative_to(root).as_posix() for n in names]
    return sorted(found)


def under(path, roots):
    return any(path == r or path.startswith(r.rstrip('/') + '/') for r in roots)


def agent_folders(cfg):
    return cfg.get('agents', {'claude': '.claude/skills'})


def feature_roots(reg, name, cfg):
    """The paths a feature owns: its paths, its skills (and their stubs), its plugin folders."""
    f = reg['features'][name]
    roots = list(f.get('paths', []))
    for s in f.get('skills', {}):
        roots.append(f'LLM/skills/{s}')
        roots += [f'{folder}/{s}' for folder in agent_folders(cfg).values()]
    roots += [f'.obsidian/plugins/{p}' for p in f.get('plugins', [])]
    roots += [f'.obsidian/snippets/{n}.css' for n in f.get('snippets', [])]
    return roots


def closure(reg, names):
    """The features plus everything they require, plus core; in registry order."""
    want, todo = {'core'}, list(names)
    while todo:
        n = todo.pop()
        if n not in reg['features']:
            sys.exit(f'unknown feature: {n} (see: folio.py list)')
        if n not in want:
            want.add(n)
            todo += reg['features'][n].get('requires', [])
    return [n for n in reg['features'] if n in want]


def enabled_of(root, reg):
    cfg = config(root)
    if cfg.get('template'):
        return list(reg['features'])
    return closure(reg, cfg.get('features', []))


def rel(target, base):
    """target relative to base when they share a folder below the drive root, else absolute."""
    target, base = Path(target).expanduser().resolve(), Path(base).resolve()
    common = Path(os.path.commonpath([target, base]))
    return os.path.relpath(target, base) if len(common.parts) > 3 else str(target)


def run_git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=check)


# ---------- settings that follow the features ----------

def apply_settings(vault, reg, enabled, src_cfg=None, dry=False):
    """Plugins, snippets, core plugins, vault_config keys and the AGENTS.md command table for `enabled`."""
    on = [reg['features'][n] for n in enabled]
    off = [f for n, f in reg['features'].items() if n not in enabled]
    obs = vault / '.obsidian'
    changes = []

    def switch(path, key, field):
        want = [x for f in on for x in f.get(field, [])]
        drop = {x for f in off for x in f.get(field, [])} - set(want)
        data = load(path) if path.exists() else ([] if key is None else {})
        cur = data if key is None else data.get(key, [])
        new = [x for x in cur if x not in drop] + [x for x in want if x not in cur]
        if new != cur:
            changes.append(f'{path.relative_to(vault)}: {", ".join(new)}')
            if key is None:
                data = new
            else:
                data[key] = new
            if not dry:
                save(path, data)

    switch(obs / 'community-plugins.json', None, 'plugins')
    switch(obs / 'appearance.json', 'enabledCssSnippets', 'snippets')

    cp = obs / 'core-plugins.json'
    if cp.exists():
        data, n0 = load(cp), len(changes)
        for f in on + off:
            for p in f.get('core_plugins', []):
                v = any(p in g.get('core_plugins', []) for g in on)
                if data.get(p) != v:
                    data[p] = v
                    changes.append(f'core plugin {p}: {"on" if v else "off"}')
        if not dry and len(changes) > n0:
            save(cp, data)

    cfg_path = vault / 'LLM' / 'vault_config.json'
    cfg = load(cfg_path)
    cfg0 = json.dumps(cfg)
    for f in off:
        for k in f.get('config', []):
            if k in cfg and not any(k in g.get('config', []) for g in on):
                del cfg[k]
                changes.append(f'vault_config: removed "{k}"')
    for f in on:
        for k in f.get('config', []):
            if k not in cfg and src_cfg and k in src_cfg:
                cfg[k] = src_cfg[k]
                changes.append(f'vault_config: added "{k}" (fill in your values)')
    if not cfg.get('template'):
        cfg['features'] = [n for n in enabled if n != 'core']
    if not dry and json.dumps(cfg) != cfg0:
        save(cfg_path, cfg)

    ag = vault / 'AGENTS.md'
    text = ag.read_text(encoding='utf-8')
    if CMD_START in text and CMD_END in text:
        rows = ['| Command | Skill | What it does |', '| :--- | :--- | :--- |']
        for f in on:
            for s, (cmd, does) in f.get('skills', {}).items():
                if (vault / 'LLM' / 'skills' / s).exists() or dry:
                    rows.append(f'| {cmd if cmd.startswith("(") else "`" + cmd + "`"} | `{s}` | {does} |')
        a, b = text.index(CMD_START) + len(CMD_START), text.index(CMD_END)
        new = text[:a] + '\n' + '\n'.join(rows) + '\n' + text[b:]
        if new != text:
            changes.append('AGENTS.md: command table')
            if not dry:
                ag.write_text(new, encoding='utf-8')
    return changes


# ---------- commands ----------

def cmd_list(a):
    reg = registry(HERE)
    cfg = config(HERE)
    enabled = enabled_of(HERE, reg)
    print('Folio template: every feature is here; a new vault picks its own.\n' if cfg.get('template')
          else f'Vault {HERE.name}: features marked [x].\n')
    for n, f in reg['features'].items():
        mark = '*' if f.get('always') else ('x' if n in enabled else ' ')
        req = f'  (needs {", ".join(f["requires"])})' if f.get('requires') else ''
        print(f'[{mark}] {n:14} {f["about"]}{req}')
        cmds = [c for c in f.get('skills', {}).values() if not c[0].startswith('(')]
        if cmds:
            print(f'    {"":14} commands: {", ".join(c[0] for c in cmds)}')


def menu(reg):
    opts = [n for n, f in reg['features'].items() if not f.get('always')]
    print('Core is always in: ' + reg['features']['core']['about'] + '.\n')
    for i, n in enumerate(opts, 1):
        f = reg['features'][n]
        req = f'  (needs {", ".join(f["requires"])})' if f.get('requires') else ''
        print(f'  {i}. {n:14} {f["about"]}{req}')
    ans = input('\nFeatures to add (numbers or names, space separated; empty = core only): ').replace(',', ' ').split()
    picked = []
    for x in ans:
        if x.isdigit() and 1 <= int(x) <= len(opts):
            picked.append(opts[int(x) - 1])
        elif x in opts:
            picked.append(x)
        else:
            sys.exit(f'not a feature: {x}')
    return picked


def cmd_new(a):
    src, reg, src_cfg = HERE, registry(HERE), config(HERE)
    if not src_cfg.get('template'):
        sys.exit('new runs from the Folio template, not from a vault (this vault: folio.py add / remove)')
    dest = Path(a.dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f'{dest} exists and is not empty')
    name = a.name or (input('Project name: ').strip() if sys.stdin.isatty() else '')
    if not name:
        sys.exit('--name is needed')
    if a.features is None:
        picked = menu(reg) if sys.stdin.isatty() else []
    else:
        picked = [x for x in a.features.replace(' ', ',').split(',') if x]
    enabled = closure(reg, picked)

    only = reg.get('template_only', [])
    owned = {n: feature_roots(reg, n, src_cfg) for n in reg['features']}
    keep, unowned = [], []
    for f in files_of(src):
        if under(f, only):
            continue
        if any(under(f, owned[n]) for n in enabled):
            keep.append(f)
        elif not any(under(f, r) for r in owned.values()):
            unowned.append(f)
    plugin_dirs = [p for n in enabled for p in reg['features'][n].get('plugins', [])]

    print(f'New vault {dest}\n  name: {name}\n  features: {", ".join(enabled)}\n  files: {len(keep)}')
    if unowned:
        print('  not in any feature (left out; add them to features.json): ' + ', '.join(unowned))
    if a.dry_run:
        return

    for f in keep:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, dest / f)
    for p in plugin_dirs:       # plugin code is gitignored: copy it when this machine has it
        d = src / '.obsidian' / 'plugins' / p
        if d.is_dir():
            shutil.copytree(d, dest / '.obsidian' / 'plugins' / p, dirs_exist_ok=True)
    theme = load(src / '.obsidian' / 'appearance.json').get('cssTheme')
    if theme and (src / '.obsidian' / 'themes' / theme).is_dir():
        shutil.copytree(src / '.obsidian' / 'themes' / theme, dest / '.obsidian' / 'themes' / theme, dirs_exist_ok=True)

    cfg_path = dest / 'LLM' / 'vault_config.json'
    cfg = load(cfg_path)
    cfg.pop('template', None)
    cfg['project'] = name
    if a.owner:
        cfg['owner'] = a.owner
    elif cfg.get('owner') in ('', 'Your Name'):
        git_name = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True).stdout.strip()
        cfg['owner'] = git_name or cfg.get('owner', '')
    if a.coauthor is not None:
        cfg['coauthor'] = a.coauthor
    if a.chat_language is not None:
        cfg['chat_language'] = a.chat_language
    cfg['projects'] = [rel(r, dest) for r in a.repo]
    cfg['folio'] = rel(src, dest)
    save(cfg_path, cfg)
    apply_settings(dest, reg, enabled, src_cfg)

    guide = dest / 'LLM' / 'LLM_GUIDE.md'
    g = guide.read_text(encoding='utf-8')
    g = g.replace('(one line: what the project is, its parts)', f'{name}: {a.about}' if a.about else name)
    if a.repo:
        g = g.replace('(repo names and where they live)', ', '.join(f'`{Path(r).name}` at `{p}`' for r, p in zip(a.repo, cfg['projects'])))
    guide.write_text(g, encoding='utf-8')
    status = dest / 'LLM' / 'DEV_STATUS.md'
    status.write_text(status.read_text(encoding='utf-8').replace('2026-01-01', datetime.date.today().isoformat()), encoding='utf-8')

    missing = [p for p in plugin_dirs if not (dest / '.obsidian' / 'plugins' / p / 'main.js').exists()]
    if not a.no_git:
        run_git(dest, 'init', '-q', '-b', 'main')
        run_git(dest, 'add', '-A')
        msg = f'chore(vault): new Folio vault for {name}\n\nFeatures: {", ".join(enabled)}'
        if cfg.get('coauthor'):
            msg += f'\n\nCo-Authored-By: {cfg["coauthor"]}'
        run_git(dest, 'commit', '-q', '-m', msg)
        print('  git: new repo on main, first commit, no remote')
    print('\nNext:')
    if missing:
        print(f'  python3 "{dest}/Tools/setup/install_plugins.py"   # plugin code: {", ".join(missing)}')
    print(f'  python3 "{dest}/Tools/setup/folio.py" link            # skills into the code repos')
    print('  open the folder in Obsidian, turn the community plugins on')
    print('  push to a private repo of your own, e.g. gh repo create <you>/' + dest.name + ' --private --source "' + str(dest) + '" --push')


def cmd_add(a):
    cfg = config(HERE)
    if cfg.get('template'):
        sys.exit('this is the template: it has every feature already')
    src = Path(a.src).expanduser() if a.src else (HERE / cfg['folio'] if cfg.get('folio') else None)
    if not src or not (src / 'Tools' / 'setup' / 'features.json').exists():
        sys.exit('template not found: pass --from <path to a Folio template clone> (git clone https://github.com/zelvet-velvet/folio)')
    src = src.resolve()
    reg, src_cfg = registry(src), config(src)
    before = enabled_of(HERE, reg)
    enabled = closure(reg, before + a.features)
    new = [n for n in enabled if n not in before]
    if not new:
        print('nothing to add: ' + ', '.join(a.features) + ' already in')
        return
    roots = [r for n in new for r in feature_roots(reg, n, src_cfg)]
    copy = [f for f in files_of(src) if under(f, roots) and not under(f, reg.get('template_only', [])) and not (HERE / f).exists()]
    print(f'add {", ".join(new)}: {len(copy)} file(s)')
    for f in copy:
        print(f'  + {f}')
        if not a.dry_run:
            (HERE / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / f, HERE / f)
    if not a.dry_run:
        for n in new:
            for p in reg['features'][n].get('plugins', []):
                d = src / '.obsidian' / 'plugins' / p
                if d.is_dir():
                    shutil.copytree(d, HERE / '.obsidian' / 'plugins' / p, dirs_exist_ok=True)
    for c in apply_settings(HERE, reg, enabled, src_cfg, a.dry_run):
        print(f'  ~ {c}')
    if not a.dry_run:
        print('Next: folio.py link (new skills into the code repos); install_plugins.py if a new plugin has no code; commit.')


def cmd_remove(a):
    reg, cfg = registry(HERE), config(HERE)
    if cfg.get('template'):
        sys.exit('this is the template: make a vault with new, then remove features there')
    before = enabled_of(HERE, reg)
    if 'core' in a.features:
        sys.exit('core cannot be removed')
    for n in a.features:
        if n not in reg['features']:
            sys.exit(f'unknown feature: {n}')
    keep = [n for n in before if n not in a.features]
    for n in keep:
        need = set(reg['features'][n].get('requires', [])) & set(a.features)
        if need:
            sys.exit(f'{n} needs {", ".join(need)}: remove {n} too, or keep it')
    gone = [n for n in a.features if n in before]
    if not gone:
        print('nothing to remove')
        return
    keep_roots = [r for n in keep for r in feature_roots(reg, n, cfg)]
    roots = [r for n in gone for r in feature_roots(reg, n, cfg)]
    files = [f for f in files_of(HERE) if under(f, roots) and not under(f, keep_roots)]
    plugin_dirs = [HERE / '.obsidian' / 'plugins' / p for n in gone for p in reg['features'][n].get('plugins', [])
                   if not any(p in reg['features'][k].get('plugins', []) for k in keep)]
    print(f'remove {", ".join(gone)}: {len(files)} file(s)' + ('' if a.yes else ' (nothing deleted: rerun with --yes)'))
    for f in files:
        print(f'  - {f}')
    for c in apply_settings(HERE, reg, keep, None, dry=not a.yes):
        print(f'  ~ {c}')
    if not a.yes:
        sys.exit(1)
    for f in files:
        (HERE / f).unlink()
    for d in plugin_dirs:
        shutil.rmtree(d, ignore_errors=True)
    for f in sorted({str(Path(f).parent) for f in files}, key=len, reverse=True):
        d = HERE / f
        while d != HERE and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    unlink_stale(HERE, cfg)
    print('Done. The files are still in git history; commit the removal.')


def git_exclude(link):
    """Add '/<path from repo root>' of a link to its repo's .git/info/exclude."""
    r = run_git(link.parent, 'rev-parse', '--show-toplevel', '--absolute-git-dir', check=False)
    if r.returncode:
        return
    top, gitdir = r.stdout.split('\n')[:2]
    entry = '/' + Path(os.path.relpath(link, top)).as_posix()
    ex = Path(gitdir) / 'info' / 'exclude'
    text = ex.read_text(encoding='utf-8') if ex.exists() else ''
    if entry not in text.splitlines():
        ex.parent.mkdir(parents=True, exist_ok=True)
        with ex.open('a', encoding='utf-8') as fh:
            fh.write(('\n' if text and not text.endswith('\n') else '') + entry + '\n')


def targets(vault, cfg, extra):
    return [(vault / os.path.expanduser(p)).resolve() for p in cfg.get('projects', [])] + [Path(r).expanduser().resolve() for r in extra]


def unlink_stale(vault, cfg, extra=(), dry=False):
    """Remove links in the code repos that point at a skill this vault no longer has."""
    src = (vault / 'LLM' / 'skills').resolve()
    for repo in targets(vault, cfg, extra):
        for folder in agent_folders(cfg).values():
            d = repo / folder
            if not d.is_dir():
                continue
            for link in d.iterdir():
                if link.is_symlink():
                    to = Path(os.path.realpath(link)) if link.exists() else Path(os.readlink(link))
                    if to.parent == src and not to.exists():
                        print(f'  stale {link} (skill gone, link removed)')
                        if not dry:
                            link.unlink()


def cmd_link(a):
    cfg = config(HERE)
    src = HERE / 'LLM' / 'skills'
    skills = sorted(p for p in src.iterdir() if (p / 'SKILL.md').exists())
    agents = agent_folders(cfg)

    for folder in agents.values():          # committed stubs in the vault itself
        for s in skills:
            stub = HERE / folder / s.name / 'SKILL.md'
            if stub.exists():
                continue
            desc = next((l[13:] for l in (s / 'SKILL.md').read_text(encoding='utf-8').splitlines() if l.startswith('description: ')), '')
            print(f'stub  {stub.relative_to(HERE)} (new: commit it)')
            if not a.dry_run:
                stub.parent.mkdir(parents=True, exist_ok=True)
                stub.write_text(STUB.format(n=s.name, d=desc), encoding='utf-8')

    linked = 0
    for repo in targets(HERE, cfg, a.repos):
        if not repo.is_dir():
            print(f'skip  {repo} (not on this machine)')
            continue
        for agent, folder in agents.items():
            if not (Path.home() / f'.{agent}').exists():
                continue                    # agent not installed here
            d = repo / folder
            print(d)
            for s in skills:
                dest = d / s.name
                if dest.is_symlink() and os.path.realpath(dest) == str(s.resolve()):
                    print(f'  ok    {s.name}')
                elif dest.is_symlink() and not dest.exists():
                    print(f'  BROKEN {s.name} -> {os.readlink(dest)} (remove it, then run again)')
                    continue
                elif dest.exists() or dest.is_symlink():
                    print(f'  KEEP  {s.name} (something else is there; left alone)')
                    continue
                else:
                    print(f'  new   {s.name}')
                    linked += 1
                    if not a.dry_run:
                        d.mkdir(parents=True, exist_ok=True)
                        dest.symlink_to(s.resolve())
                if not a.dry_run:
                    git_exclude(dest)
    unlink_stale(HERE, cfg, a.repos, a.dry_run)

    for g in [Path.home() / x for x in ('.claude/skills', '.codex/skills', '.agents/skills')]:
        if not g.is_dir():
            continue                         # older versions linked into the global folders
        for link in g.iterdir():
            if not link.is_symlink():
                continue
            to = os.readlink(link)
            if to.startswith(str(src.resolve())):
                why = 'global link to this vault'
            elif not link.exists() and '/LLM/skills/' in to:
                why = 'broken global link to a vault that moved'
            else:
                continue
            if a.remove_global and not a.dry_run:
                link.unlink()
                print(f'removed {why}: {link}')
            else:
                print(f'OLD   {why}: {link} -> {to} (--remove-global deletes it)')
    print(f'{"would link" if a.dry_run else "linked"} {linked} new skill link(s)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='features, and which ones this vault has')
    p = sub.add_parser('new', help='make a project vault (from the template)')
    p.add_argument('dest')
    p.add_argument('--name')
    p.add_argument('--features', help='comma separated; core is always in. Omit on a terminal for a menu')
    p.add_argument('--repo', action='append', default=[], help='a code repo this vault logs (repeatable)')
    p.add_argument('--owner')
    p.add_argument('--chat-language')
    p.add_argument('--coauthor', help='commit attribution line of the agent that keeps this vault ("" for none)')
    p.add_argument('--about', help='one line: what the project is')
    p.add_argument('--no-git', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('add', help='add features to this vault')
    p.add_argument('features', nargs='+')
    p.add_argument('--from', dest='src')
    p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('remove', help='remove features from this vault')
    p.add_argument('features', nargs='+')
    p.add_argument('--yes', action='store_true')
    p = sub.add_parser('link', help='skill stubs in the vault, skill links in the code repos')
    p.add_argument('repos', nargs='*')
    p.add_argument('--remove-global', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    {'list': cmd_list, 'new': cmd_new, 'add': cmd_add, 'remove': cmd_remove, 'link': cmd_link}[a.cmd](a)


if __name__ == '__main__':
    main()
