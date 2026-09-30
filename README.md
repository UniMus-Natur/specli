# specli

Specify 7 GitOps CLI: **scope-first** pull/push of forms (whole viewsets),
schema config, and app resources (UIFormatters, DataObjFormatters, WebLinks).

The workspace layout mirrors Specify’s hierarchy:

`Personal → UserType → Collection → Discipline → Common → Backstop`

**Backstop** (and other disk defaults) are pulled from the live instance via
`/context/viewsets.json` + `/static/config/…` — no Specify source tree required.
Backstop is kept in the workspace as the edit baseline; it is **not** pushed
(promote edits into `base/` / `by/*` instead).

specli binds a **directory** to a context; whether that directory is a git repo
is up to you.

**New here?** See [GETTING_STARTED.md](GETTING_STARTED.md).

## Install

```bash
pip install "specli @ git+https://github.com/UniMus-Natur/specli.git"
# or: pip install -e .
specli --version
```

Requires Python 3.9+.

## Quick start

```bash
specli auth login
specli workspace init ./specify-config
specli pull --clean
specli status
specli push
```

## Workspace layout

```text
backstop/                                  # stock disk baseline (from instance)
  forms/<viewset>.views.xml
  app/UIFormatters.xml
base/                                      # Common (disk + DB)
  forms/…
  app/…
by/
  discipline/<slug>/
    forms/…  schema/…  app/…
  collection/<slug>/
  usertype/<discipline>/<usertype>/
  user/<username>/                         # Personal; --include-personal
```

| Concept | Meaning |
|---------|---------|
| **Context** | Specify instance (URL + user + secret) |
| **Workspace** | Directory bound on the context |
| **Scope** | One hierarchy level (`backstop`, `common`, `discipline/botany`, …) |

Default pull covers Backstop + Common + Discipline + Collection + UserType.
Personal needs `--include-personal`.

## Commands

```bash
specli auth login|logout|whoami
specli config get-contexts|use-context|set-context|…

specli workspace init ./path
specli workspace set ./path
specli workspace get|status

specli pull [--clean] [--include-personal] [--scope …]
specli push [--dry-run] [--include-personal]
specli status

specli form pull|push|status
specli schema pull|push|status
specli app pull|push|status
```

Passwords: OS keyring (fallback `~/.specli/credentials.yaml`).

CI: `SPECLI_URL` + `SPECLI_USER` + `SPECLI_PASSWORD` (+ optional
`SPECLI_LOGIN_COLLECTION`, `SPECLI_WORKSPACE`).

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)).
