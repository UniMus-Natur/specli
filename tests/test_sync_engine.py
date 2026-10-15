from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specli.repository_v3 import (
    ContextState,
    Inventory,
    ScopeSelector,
    init_repository,
    write_viewset,
)
from specli.sync_engine import (
    ResolverContext,
    ResourceState,
    ViewProvider,
    compare_three_way,
    local_resource_states,
    resolve_effective_views,
    staged_repository,
    validate_repository,
)

from test_form_family import VIEWSET


def state(uid: str, digest: str) -> ResourceState:
    return ResourceState(uid=uid, canonical_sha256=digest)


class StatusTests(unittest.TestCase):
    def test_three_way_classification(self) -> None:
        before = {
            "local": state("local", "sha256:a"),
            "remote": state("remote", "sha256:a"),
            "conflict": state("conflict", "sha256:a"),
        }
        local = {
            "local": state("local", "sha256:b"),
            "remote": state("remote", "sha256:a"),
            "conflict": state("conflict", "sha256:b"),
            "create": state("create", "sha256:c"),
        }
        remote = {
            "local": state("local", "sha256:a"),
            "remote": state("remote", "sha256:c"),
            "conflict": state("conflict", "sha256:c"),
        }
        result = {entry.uid: entry.kind for entry in compare_three_way(before, local, remote)}
        self.assertEqual(
            result,
            {
                "conflict": "conflict",
                "create": "create",
                "local": "local-update",
                "remote": "remote-drift",
            },
        )

    def test_absence_is_not_deletion(self) -> None:
        with self.assertRaises(SystemExit):
            compare_three_way(
                {"x": state("x", "sha256:a")},
                {},
                {"x": state("x", "sha256:a")},
            )


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ResolverContext(
            discipline="karplanter-moser",
            discipline_type="botany",
            collection="nhm-karplanter",
            user_type="manager",
            username="alice",
        )

    def provider(
        self,
        source: str,
        scope: ScopeSelector,
        path: str,
        order: int = 0,
    ) -> ViewProvider:
        return ViewProvider(
            view_name="CollectionObject",
            source=source,
            scope=scope,
            viewset_name=path,
            manifest_path=path,
            canonical_sha256=f"sha256:{path}",
            registry_order=order,
        )

    def test_current_specify_prefers_any_db_over_disk(self) -> None:
        common_db = self.provider(
            "managed", ScopeSelector(level="common"), "common-db"
        )
        discipline_disk = self.provider(
            "vendor",
            ScopeSelector(level="discipline-type", discipline_type="botany"),
            "botany-disk",
        )
        effective = resolve_effective_views(
            [discipline_disk, common_db],
            context=self.context,
        )
        self.assertEqual(
            effective["CollectionObject"].manifest_path,
            "common-db",
        )

    def test_same_db_precedence_is_rejected(self) -> None:
        providers = [
            self.provider("managed", ScopeSelector(level="common"), "one"),
            self.provider("managed", ScopeSelector(level="common"), "two"),
        ]
        with self.assertRaises(SystemExit):
            resolve_effective_views(providers, context=self.context)


class RepositoryValidationTests(unittest.TestCase):
    def build(self, root: Path) -> None:
        init_repository(root, instance="https://example.test")
        manifest, entry = write_viewset(
            root,
            xml=VIEWSET,
            source="managed",
            scope=ScopeSelector(level="common"),
            known_unresolved=[],
        )
        inventory = Inventory([entry])
        inventory.write(root)
        states = local_resource_states(root)
        ContextState(
            context="staging",
            instance="https://example.test",
            resolver_contract="specify7/current",
            resources={
                uid: {"canonicalSha256": resource.canonical_sha256}
                for uid, resource in states.items()
            },
        ).write(root)
        self.assertTrue(manifest.is_file())

    def test_valid_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root)
            validate_repository(root)

    def test_unknown_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root)
            unknown = root / "forms/surprise.xml"
            unknown.parent.mkdir(parents=True, exist_ok=True)
            unknown.write_text("<x/>")
            with self.assertRaises(SystemExit):
                validate_repository(root)

    def test_staged_replace_does_not_publish_invalid_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "repo"
            self.build(destination)
            old_inventory = (destination / "inventory.yaml").read_text()

            def bad_builder(stage: Path) -> None:
                init_repository(stage)
                (stage / "forms").mkdir()
                (stage / "forms/bad.xml").write_text("<bad/>")

            with self.assertRaises(SystemExit):
                staged_repository(destination, bad_builder)
            self.assertEqual(
                (destination / "inventory.yaml").read_text(),
                old_inventory,
            )


if __name__ == "__main__":
    unittest.main()
