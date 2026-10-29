# Getting started

This guide is for **any** Specify 7 instance. `specli` is the CLI; your
**config repository** is a normal Git repo you create (or already have) for
that instance’s forms and related configuration.

## 1. Install the CLI

Pick one:

```bash
# pipx — best for a global `specli` command on a laptop
pipx install "specli @ git+https://github.com/UniMus-Natur/specli.git"

# pip — user install
python3 -m pip install --user "specli @ git+https://github.com/UniMus-Natur/specli.git"

# venv
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "specli @ git+https://github.com/UniMus-Natur/specli.git"
```

Check:

```bash
specli --version
```

Needs Python 3.9+.

## 2. Create a config repository

```bash
mkdir my-specify-config
cd my-specify-config
git init

specli workspace init .
```

You will be asked for:

| Field | Purpose |
|-------|---------|
| Specify URL | Base URL of the instance, e.g. `https://specify.example.org` |
| Login collection | Only the collection used to open an API session — not the sync scope |

That writes:

- `workspace.yaml` — URL / login collection / policies (**commit this**; no username)
- `inventory.yaml` — empty until the first pull
- `.gitignore` — ignores `.specli.local.yaml`

Non-interactive:

```bash
specli workspace init . \
  --url https://specify.example.org \
  --login-collection MyCollection
```

## 3. Log in (credentials stay local)

```bash
specli auth login
```

Username and password are stored only in `.specli.local.yaml` (mode `0600`,
gitignored). Each clone / teammate uses their own account. Re-run login after
cloning the repo on another machine.

```bash
specli auth whoami --check
```

## 4. First pull

```bash
specli pull
```

This builds a full snapshot (forms, app resources, schema, picklists), validates
it, then publishes it into the current directory. Then:

```bash
git add .
git status    # confirm .specli.local.yaml is NOT listed
git commit -m "Initial Specify configuration snapshot"
```

## 5. Everyday use

Always run from inside the config repo (or set `SPECLI_WORKSPACE`):

```bash
cd my-specify-config

specli pull                 # refresh from the instance
# edit forms / schema / resources / picklists …

specli gitops validate
specli status               # three-way: baseline / local / remote
specli push --dry-run
specli push                 # stock Specify HTTP APIs
```

### Status exit codes

| Code | Meaning |
|------|---------|
| `0` | Local, baseline, and remote agree |
| `2` | Deployable local changes |
| `3` | Conflict or remote drift — do not push |

### Explicit deletion

Deleting a file alone is **not** a delete. To retire a managed resource:

1. Set its `inventory.yaml` entry to `status: absent` with a reason.
2. Remove its owned files.
3. `specli status` then `specli push`.

## Multiple instances / machines

- One config **Git repository per Specify instance** is the usual model.
- Each clone stores its own `.specli.local.yaml` (user + password); never commit it.
- Point automation at a checkout with `SPECLI_WORKSPACE` and env credentials
  (see README).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No specli/v3 repository found` | `cd` into the config repo, or set `SPECLI_WORKSPACE` |
| `No credentials in .specli.local.yaml` | `specli auth login` |
| Push rejected (conflict / remote-drift) | `specli pull` or resolve local vs remote, then push again |
| Push fails mid-way | Ops are sequential; inspect remote, fix forward or restore from git |
| Pull refuses local edits | Commit or revert local changes first; pull never discards work |
