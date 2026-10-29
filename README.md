# specli

GitOps CLI for [Specify 7](https://github.com/specify/specify7) configuration.

Use it with **any** Specify 7 instance. The tool is separate from your config
repository: install `specli` once, then keep each instance’s forms, schema, app
resources, and picklists in its own Git repo.

- Instance URL (and optional login collection) live in the config repo (`workspace.yaml`)
- Username + password stay local and gitignored (`.specli.local.yaml`)
- No `~/.specli` / machine-global config
- Pull / status / push all use Specify’s stock HTTP APIs (no server patch)

Full contract: [docs/GITOPS_V3.md](docs/GITOPS_V3.md)  
Walkthrough: [GETTING_STARTED.md](GETTING_STARTED.md)

## Install

Requires **Python 3.9+**.

**Recommended — isolated CLI with [pipx](https://pipx.pypa.io/):**

```bash
pipx install "specli @ git+https://github.com/UniMus-Natur/specli.git"
specli --version
```

**pip (user or venv):**

```bash
python3 -m pip install --user "specli @ git+https://github.com/UniMus-Natur/specli.git"
# or
python3 -m venv .venv && source .venv/bin/activate
pip install "specli @ git+https://github.com/UniMus-Natur/specli.git"
```

**From a clone (development):**

```bash
git clone https://github.com/UniMus-Natur/specli.git
cd specli
pip install -e .
```

Upgrade later with `pipx upgrade specli` or `pip install -U "specli @ git+…"`.

## Quick start (any instance)

```bash
mkdir my-specify-config && cd my-specify-config
git init

specli workspace init .     # prompts for URL / login collection
specli auth login           # stores user + password in .specli.local.yaml
specli pull                 # snapshot the instance into this repo

git add .
git commit -m "Initial Specify config snapshot"
```

Work from inside that directory (or set `SPECLI_WORKSPACE` to its path):

```bash
specli auth whoami --check
specli status
specli push --dry-run
specli push                 # writes via Specify REST / context APIs
```

## What lives in the config repo

```text
workspace.yaml                 # committed: instance URL, loginCollection, policies
.specli.local.yaml             # gitignored: user + password
.gitignore
inventory.yaml

forms/…  viewsets/…  schema/…  resources/…  records/…  state/
```

| Concept | Meaning |
|---------|---------|
| **Config repo** | Git repository for one Specify instance’s desired state |
| **`workspace.yaml`** | Shared instance binding + policies (safe to commit) |
| **`.specli.local.yaml`** | Per-clone username + password (never commit) |
| **vendor** | Exact disk defaults from Specify (read-only) |
| **managed** | Exact DB-backed resources you own |

Forms are edited as self-contained **families** (view + its viewdefs together).
Manifests under `viewsets/` define deployment boundaries. See
[GITOPS_V3.md](docs/GITOPS_V3.md).

## Commands

```bash
specli workspace init|get|status
specli auth login|logout|whoami

specli pull [--include-personal]
specli status [--include-personal]
specli push [--dry-run] [--include-personal]
specli gitops validate
```

`status` exit codes: `0` clean · `2` local changes · `3` conflict / remote drift

## CI / automation

Prefer env vars over a secrets file:

```bash
export SPECLI_URL=https://specify.example.org
export SPECLI_USER=deploy
export SPECLI_PASSWORD=…
export SPECLI_LOGIN_COLLECTION=MyCollection   # optional API door
export SPECLI_WORKSPACE=/path/to/config-repo
specli status
specli push
```

## Push notes

`push` applies create / update / delete through Specify’s public REST and
context endpoints (`SpAppResource`, `SpViewSetObj`, picklists, schema import).
It refuses conflict / remote-drift from `status` first, and checks resource
`version` before each update. Operations are sequential (not one DB
transaction): if a later op fails, earlier ones may already have landed — fix
forward or restore from git and push again.

## License

GPL-3.0-or-later.
