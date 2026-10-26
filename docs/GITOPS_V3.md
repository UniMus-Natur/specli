# Specify GitOps v3

Format contract for a **config repository**: one Git checkout of desired-state
forms, schema, app resources, and picklists for a Specify 7 instance. The
layout is the same for every institution; only `workspace.yaml` and the
resource contents change.

Install and day-to-day commands: [../README.md](../README.md),
[../GETTING_STARTED.md](../GETTING_STARTED.md).

## Goals

The repository is a complete, reviewable desired-state representation of one
Specify instance (or a deliberate multi-context snapshot of one deployment). It must:

- make forms easy to find and edit;
- preserve the exact scope and source of every resource;
- compile to complete Specify deployment units;
- detect remote drift before writing;
- make deletion and fallback behavior explicit;
- inventory unsupported or excluded resources instead of silently omitting them.

The format has no compatibility contract with earlier specli workspaces.

## Repository layout

```text
workspace.yaml
inventory.yaml

forms/<table>/<family>/
  vendor/<disk-scope>/<viewset>.xml
  managed/<scope>/<viewset>.xml

viewsets/
  vendor/<disk-scope>/<viewset>.yaml
  managed/<scope>/<viewset>.yaml

schema/managed/<discipline>/<language>/<table>.json

resources/<logical-name>/
  vendor/<scope>.<extension>
  managed/<scope>.<extension>

records/picklists/<scope>/<name>.json
state/<context>.lock.yaml
```

`vendor` contains exact disk defaults and is read-only. `managed` contains
complete DB-backed desired resources. Managed resources are not patches.

## Form families

A form family is one connected component in a viewset's reference graph:

```text
view --altview/@viewdef--> viewdef --definition--> viewdef
```

All views and viewdefs in the component are stored in one mini-viewset XML
file. This keeps shared definitions next to every view that uses them without
duplicating XML or creating `_shared` references.

For example, the family file below contains `CollectionObject`,
`CollectionObjectMini`, and their common `Collection Object` viewdef:

```text
forms/collectionobject/collection-object/
  vendor/discipline-type/botany/botany-views.xml
  managed/discipline/herbarium/herbarium-standard.xml
```

The family path is an editing index, not a deployment boundary.

## Viewset manifests

A viewset manifest is the authoritative deployment boundary:

```yaml
apiVersion: specli/v3
kind: Viewset
metadata:
  name: Herbarium - standard
  source: managed
scope:
  level: discipline
  discipline: herbarium
rootAttributes:
  name: Herbarium - standard
members:
  - forms/collectionobject/collection-object/managed/discipline/herbarium/herbarium-standard.xml
knownIssues:
  unresolvedViewdefs: []
deletions: []
```

Membership order controls assembly order. Filesystem enumeration never does.
Unreferenced viewdefs, if present, live beside the manifest in `orphans.xml`;
they are never hidden among editable form families.

## Scope identity

Desired state uses stable natural selectors:

- discipline: type and name;
- collection: code plus owning discipline;
- user type: normalized user type plus collection and discipline;
- personal: username plus collection and discipline;
- common and backstop: singleton selectors.

Numeric database IDs, row versions, timestamps, ETags, and observed hashes are
environment state and belong only in `state/<context>.lock.yaml`.

## Resource model

Different resource types use their natural review unit:

- forms: self-contained families plus a whole-viewset manifest;
- schema localization: one JSON file per table and language;
- app resources, reports, labels, and preferences: one whole logical document;
- picklists: one typed JSON record containing definition and ordered items.

Every discovered resource appears in `inventory.yaml` as `vendor`, `managed`,
or `excluded`. Exclusions require a policy and reason.

## Canonical XML

- XML 1.0, UTF-8, LF line endings.
- Reject DTDs, external entities, and XInclude.
- Preserve comments, processing instructions, unknown attributes, and element
  order.
- Use manifest order for top-level views and viewdefs.
- Canonical hashes use a documented C14N profile; indentation and attribute
  formatting are not identity.
- Pull, split, assemble, and split again must produce identical canonical
  family files and hashes.
- Lossy filenames are disambiguated with a deterministic hash suffix.

## Validation

The entire operation fails before changing local or remote state when it finds:

- an unknown file or unlisted inventory resource;
- duplicate scope selectors, viewsets, views, viewdefs, or case-folded names;
- missing manifest members or files owned by multiple manifests;
- malformed XML or the wrong root element;
- dangling `altview/@viewdef` or `<definition>` references;
- cyclic definition references;
- a view whose class disagrees with its table path;
- multiple or missing `SpAppResourceData` rows;
- unmanaged resources colliding with managed identities;
- an undeclared resolver contract;
- an implicit deletion.

Existing dangling references can be imported only as an exact,
manifest-declared `knownIssues` baseline. New or changed defects fail.

## Resolver contract

Specify currently resolves the two major resource classes differently:

- views: every DB hierarchy level, then every disk hierarchy level;
- app resources: DB then disk at each hierarchy level.

specli records and simulates the running server's resolver contract. Status
shows effective-provider changes and changes that are fully shadowed.

## Pull

1. Fetch capabilities and resolver contract.
2. Read a consistent remote snapshot.
3. Write into a staging directory.
4. Parse, split, validate, and reassemble every resource.
5. Verify canonical hashes.
6. Abort on local edits, incomplete reads, or remote ambiguity.
7. Atomically replace only the selected local trees and update observed state.

Pull refuses to overwrite uncommitted local edits. Resolve or commit those
first, then pull again.

When a disk registry (`app_resources.xml` / `viewset_registry.xml`) lists a
file that the server returns as HTTP 404, pull warns on stderr and records an
`excluded` inventory entry with reason
`dangling disk registry reference (HTTP 404): …` instead of aborting. Other
HTTP failures for registry-listed files remain fatal.

## Status

Status compares:

- baseline: `state/<context>.lock.yaml`;
- desired: compiled local documents;
- remote: a fresh snapshot.

It reports clean, local update, remote drift, conflict, create, explicit delete,
unmanaged collision, shadowed/no-effect change, and effective-provider change.

Exit codes:

- `0`: clean;
- `2`: valid changes planned;
- `3`: conflict, invalid configuration, or drift;
- `4`: connectivity or capability failure.

## Push

All payloads are compiled and validated before the first write. Production push
uses one transactional compare-and-swap deployment request containing:

- an idempotency key derived from the Git commit and plan hash;
- expected object IDs, versions, and canonical hashes;
- complete desired payloads;
- explicit deletion and fallback assertions;
- expected instance and resolver contract.

The server locks affected rows, validates all preconditions, applies all
operations in one transaction, records an audit event, and returns new state
tokens. Generic sequential REST writes are best-effort and are not considered
production-safe.

## Deletion

Absence is corruption, not deletion. A deletion needs a tombstone with:

- the previous canonical hash;
- the exact object or member being removed;
- the expected newly exposed provider and hash, or explicit `none`.

Deletion is rejected if the remote object or fallback has changed.
