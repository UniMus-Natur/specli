# Getting started with specli

Bootstrap a config directory that mirrors a live Specify 7 instance using
Specify’s own hierarchy (scope-first). Version control is optional and entirely
up to you — specli only reads/writes files in the bound directory.

## Install

```bash
pip install "specli @ git+https://github.com/UniMus-Natur/specli.git"
# or from a clone: pip install -e .
specli --version
```

## 1. Log in

```bash
specli auth login
```

A **context** is one Specify instance. **login-collection** is only an API door.

## 2. Bind a workspace directory

```bash
specli workspace init ./specify-config
```

Creates a scope-first layout and `specli.yaml`, and points the current context
at that directory:

```text
specify-config/
  specli.yaml
  backstop/                     # stock Specify disk baseline
    forms/
    app/
  base/                         # Common
    forms/
    app/
  by/
    discipline/<slug>/
      forms/
      schema/
      app/
    collection/<slug>/
    usertype/<discipline>/<usertype>/
    user/<username>/            # Personal — only with --include-personal
```

Or bind an existing directory:

```bash
specli workspace set ./specify-config
```

## 3. Pull (parity)

```bash
specli pull --clean
```

Pulls **whole documents** for default scopes: Backstop, Common, Discipline,
Collection, UserType.

Disk layers (Backstop, stock Common, discipline FS viewsets) come from the
instance itself:

* `GET /context/viewsets.json` — list of `*.views.xml` under the server config tree
* `GET /static/config/<path>` — raw XML (same URLs the Form Editor uses)

DB layers overlay on top of that.

| Path | Contents |
|------|----------|
| `backstop/forms/…` | Stock system/search/global viewsets (+ formatters) |
| `base/forms/…` | Common disk + any Common DB viewsets |
| `by/discipline/<slug>/forms/…` | Discipline disk + DB viewsets |
| `by/discipline/<slug>/schema/` | Schema JSON |
| `by/*/app/…` | App resources |

**Personal** (`by/user/…`) needs `--include-personal`.

Backstop is **not** pushed (server disk stock). Promote edits into `base/` or
`by/…`, then `specli push`.

## 4. Day-to-day

```bash
specli form status
specli form push

specli schema push --discipline botany
specli app push --scope discipline/botany

specli status          # dry-run whole workspace
specli push            # apply whole workspace
```

Narrow with `--scope` / `--discipline`:

```bash
specli pull --scope discipline/botany
specli form pull --scope collection/my-coll
```

## Mental model

Specify resolves config **Personal → UserType → Collection → Discipline → Common → Backstop**.

specli stores the same layers under `backstop/`, `base/`, and `by/…`. Each file
is a **whole** viewset or app-resource document. Disk stock (including Backstop)
is pulled from the live instance over HTTP; push targets DB scopes only
(`base/` / `by/*`).
