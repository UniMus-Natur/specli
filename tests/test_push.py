from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specli.push import apply_push_plan, build_push_plan
from specli.repository_v3 import (
    ContextState,
    Inventory,
    InventoryEntry,
    ScopeSelector,
    init_repository,
    write_resource,
)


def payload_hash(payload: str) -> str:
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class PushPlanTests(unittest.TestCase):
    def repositories(self, local: Path, remote: Path, *, local_payload: str):
        scope = ScopeSelector(
            level="collection", discipline="botany", collection="plants"
        )
        states = {}
        entries = []
        for root, payload in ((remote, "<old/>"), (local, local_payload)):
            init_repository(root, instance="https://example.test")
            _, entry = write_resource(
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

    def test_builds_update_with_version_preconditions(self) -> None:
        with tempfile.TemporaryDirectory() as local_tmp, tempfile.TemporaryDirectory() as remote_tmp:
            local, remote = Path(local_tmp), Path(remote_tmp)
            entry, states = self.repositories(local, remote, local_payload="<new/>")
            baseline = ContextState(
                context="test",
                instance="https://example.test",
                resolver_contract="test",
                resources=states,
            )
            plan, uids = build_push_plan(local, remote, baseline=baseline)
            self.assertEqual(uids, [entry.uid])
            self.assertNotIn("idempotencyKey", plan)
            self.assertNotIn("apiVersion", plan)
            operation = plan["operations"][0]
            self.assertEqual(operation["action"], "update")
            self.assertEqual(operation["expected"]["objectVersion"], 2)
            self.assertEqual(operation["expected"]["dataVersion"], 3)
            self.assertEqual(operation["desired"]["payload"], "<new/>")
            self.assertEqual(operation["desired"]["directoryId"], 4)

    def test_explicit_tombstone_builds_delete(self) -> None:
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
            plan, _ = build_push_plan(local, remote, baseline=baseline)
            self.assertEqual(plan["operations"][0]["action"], "delete")
            self.assertEqual(plan["operations"][0]["desired"], {})


class ApplyPushPlanTests(unittest.TestCase):
    def test_updates_app_resource_over_rest(self) -> None:
        plan = {
            "operations": [
                {
                    "kind": "AppResource",
                    "action": "update",
                    "expected": {
                        "objectId": 10,
                        "dataId": 11,
                        "objectVersion": 2,
                        "dataVersion": 3,
                        "directoryId": 4,
                    },
                    "desired": {
                        "directoryId": 4,
                        "name": "WebLinks",
                        "mimetype": "text/xml",
                        "level": 0,
                        "payload": "<new/>",
                    },
                }
            ]
        }

        class FakeSession:
            def __init__(self) -> None:
                self.cookies = {"csrftoken": "tok"}

            def close(self) -> None:
                return None

        conn = mock.Mock()
        conn.url = "https://example.test"
        conn.session.return_value = (FakeSession(), "plants", 1, None)

        with (
            mock.patch(
                "specli.push.get_json",
                side_effect=[
                    {"id": 1, "name": "superuser"},
                    {
                        "id": 10,
                        "name": "WebLinks",
                        "mimetype": "text/xml",
                        "version": 2,
                        "level": 0,
                        "spappresourcedir": "/api/specify/spappresourcedir/4/",
                        "specifyuser": "/api/specify/specifyuser/1/",
                        "resource_uri": "/api/specify/spappresource/10/",
                    },
                    {
                        "id": 11,
                        "data": "<old/>",
                        "version": 3,
                        "spappresource": "/api/specify/spappresource/10/",
                        "spviewsetobj": None,
                        "resource_uri": "/api/specify/spappresourcedata/11/",
                    },
                ],
            ),
            mock.patch(
                "specli.push.put_json",
                side_effect=[
                    {"id": 10, "version": 3},
                    {"id": 11, "version": 4},
                ],
            ) as put,
        ):
            results = apply_push_plan(conn, plan)

        self.assertEqual(results[0]["action"], "update")
        self.assertEqual(results[0]["objectId"], 10)
        self.assertEqual(results[0]["dataVersion"], 4)
        self.assertEqual(put.call_count, 2)
        self.assertIn("<new/>", put.call_args_list[1].args[3]["data"])


if __name__ == "__main__":
    unittest.main()
