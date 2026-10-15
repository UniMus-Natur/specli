# specli

GitOps CLI for [Specify 7](https://github.com/specify/specify7) configuration.

Use it with **any** Specify 7 instance. The tool is separate from your config
repository: install `specli` once, then keep each instance’s forms, schema, app
resources, and picklists in its own Git repo.

- Connection settings live in the config repo (`workspace.yaml`)
- Password stays local and gitignored (`.specli.local.yaml`)
- No `~/.specli` / machine-global config
- Pull is staged and validated; push is all-or-nothing CAS (requires a small
  Specify server endpoint)

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

specli workspace init .     # prompts for URL, user, login collection
specli auth login           # stores password in .specli.local.yaml
specli pull                 # snapshot the instance into this repo

git add .
git commit -m "Initial Specify config snapshot"
```

Work from inside that directory (or set `SPECLI_WORKSPACE` to its path):

```bash
specli auth whoami --check
specli status
specli push --dry-run
specli push                 # needs /api/gitops/deploy/ on the server
```

## What lives in the config repo

```text
workspace.yaml                 # committed: instance URL, user, loginCollection
.specli.local.yaml             # gitignored: password only
.gitignore
inventory.yaml

forms/…  viewsets/…  schema/…  resources/…  records/…  state/
```

| Concept | Meaning |
|---------|---------|
| **Config repo** | Git repository for one Specify instance’s desired state |
| **`workspace.yaml`** | Non-secret connection + policies (safe to commit) |
| **`.specli.local.yaml`** | Password for this checkout only (never commit) |
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

## Server requirement for push

`pull` / `status` work against stock Specify 7 HTTP APIs.

**Production `push`** requires the transactional endpoint
`POST /api/gitops/deploy/` (compare-and-swap, one DB transaction). Without it,
specli will not fall back to unsafe sequential REST writes. Ship that endpoint
with your Specify deployment (see this project’s Specify patch / docs).

## License

GPL-3.0-or-later.
