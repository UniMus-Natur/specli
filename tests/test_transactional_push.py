from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from specli.repository_v3 import (
    ContextState,
    Inventory,
    InventoryEntry,
    ScopeSelector,
    init_repository,
    write_resource,
)
from specli.transactional_push import build_deployment


def payload_hash(payload: str) -> str:
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class TransactionalPushTests(unittest.TestCase):
    def repositories(self, local: Path, remote: Path, *, local_payload: str):
        scope = ScopeSelector(
            level="collection", discipline="botany", collection="plants"
        )
        states = {}
        entries = []
        for root, payload in ((remote, "<old/>"), (local, local_payload)):
            init_repository(root, instance="https://example.test")
            manifest, entry = write_resource(
                root,
                name="WebLinks",
                payload=payload,
                mimetype="text/xml",
                source="managed",
                scope=scope,
                metadata={"level": 0},
            )
            Inventory([entry]).write(root)
            entries.append(entry)
            if root == remote:
                states[entry.uid] = {
                    "canonicalSha256": payload_hash(payload),
                    "objectId": 10,
                    "dataId": 11,
                    "objectVersion": 2,
                    "version": 3,
                    "directoryId": 4,
                }
                ContextState(
                    context="test",
                    instance="https://example.test",
                    resolver_contract="test",
                    resources=states,
                ).write(root)
        return entries[0], states

    def test_builds_update_with_cas_preconditions(self):
        with tempfile.TemporaryDirectory() as local_tmp, tempfile.TemporaryDirectory() as remote_tmp:
            local, remote = Path(local_tmp), Path(remote_tmp)
            entry, states = self.repositories(local, remote, local_payload="<new/>")
            baseline = ContextState(
                context="test",
                instance="https://example.test",
                resolver_contract="test",
                resources=states,
            )
            request, uids = build_deployment(local, remote, baseline=baseline)
            self.assertEqual(uids, [entry.uid])
            operation = request["operations"][0]
            self.assertEqual(operation["action"], "update")
            self.assertEqual(operation["expected"]["objectVersion"], 2)
            self.assertEqual(operation["expected"]["dataVersion"], 3)
            self.assertEqual(operation["desired"]["payload"], "<new/>")
            self.assertEqual(operation["desired"]["directoryId"], 4)

    def test_explicit_tombstone_builds_delete(self):
        with tempfile.TemporaryDirectory() as local_tmp, tempfile.TemporaryDirectory() as remote_tmp:
            local, remote = Path(local_tmp), Path(remote_tmp)
            entry, states = self.repositories(local, remote, local_payload="<old/>")
            Inventory(
                [
                    InventoryEntry(
                        uid=entry.uid,
                        kind=entry.kind,
                        source=entry.source,
                        status="absent",
                        path=None,
                        scope=entry.scope,
                        reason="retired",
                    )
                ]
            ).write(local)
            manifest = next(local.glob("resources/**/resource.yaml"))
            data = next(
                path
                for path in manifest.parent.iterdir()
                if path.name.startswith("resource.") and path.suffix != ".yaml"
            )
            manifest.unlink()
            data.unlink()
            baseline = ContextState(
                context="test",
                instance="https://example.test",
                resolver_contract="test",
                resources=states,
            )
            request, _ = build_deployment(local, remote, baseline=baseline)
            self.assertEqual(request["operations"][0]["action"], "delete")
            self.assertEqual(request["operations"][0]["desired"], {})


if __name__ == "__main__":
    unittest.main()
