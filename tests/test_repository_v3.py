from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from specli.form_family import canonical_sha256
from specli.repository_v3 import (
    ContextState,
    Inventory,
    ScopeSelector,
    compile_schema,
    compile_viewset,
    init_repository,
    split_schema,
    write_picklist,
    write_resource,
    write_viewset,
)

from test_form_family import VIEWSET


class RepositoryV3Tests(unittest.TestCase):
    def test_viewset_paths_are_form_first_and_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repository(root, instance="https://example.test")
            scope = ScopeSelector(
                level="discipline",
                discipline="Karplanter Moser",
            )
            manifest, entry = write_viewset(
                root,
                xml=VIEWSET,
                source="managed",
                scope=scope,
                known_unresolved=[],
            )
            self.assertEqual(
                manifest.relative_to(root).as_posix(),
                "viewsets/managed/discipline/karplanter-moser/example.yaml",
            )
            family = (
                root
                / "forms/collectionobject/collection-object/managed/"
                "discipline/karplanter-moser/example.xml"
            )
            self.assertTrue(family.is_file())
            name, assembled = compile_viewset(root, manifest)
            self.assertEqual(name, "Example")
            self.assertEqual(canonical_sha256(assembled), canonical_sha256(VIEWSET))

            inventory = Inventory()
            inventory.add(entry)
            inventory.write(root)
            loaded = Inventory.load(root)
            self.assertEqual([item.uid for item in loaded.entries], [entry.uid])

    def test_unresolved_baseline_must_be_explicit(self) -> None:
        broken = """<viewset name="broken"><views>
        <view name="A" class="x.A"><altviews>
          <altview name="A" viewdef="Missing"/>
        </altviews></view></views><viewdefs/></viewset>"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                write_viewset(
                    Path(tmp),
                    xml=broken,
                    source="managed",
                    scope=ScopeSelector(level="common"),
                )

    def test_schema_splits_per_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, _ = split_schema(
                root,
                discipline="botany",
                language="en",
                schema={"agent": {"name": "Agent"}, "collectionobject": {"name": "CO"}},
                metadata={"disciplineId": 4},
            )
            self.assertTrue((manifest.parent / "agent.json").is_file())
            self.assertEqual(
                compile_schema(root, manifest),
                {
                    "agent": {"name": "Agent"},
                    "collectionobject": {"name": "CO"},
                },
            )

    def test_generic_resources_and_picklists_are_inventoriable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = ScopeSelector(
                level="collection",
                discipline="botany",
                collection="NHM-karplanter",
            )
            resource, resource_entry = write_resource(
                root,
                name="Karplanteetikett 8",
                payload="<report/>",
                mimetype="text/xml",
                source="managed",
                scope=scope,
            )
            picklist, picklist_entry = write_picklist(
                root,
                scope=scope,
                name="TypeStatus",
                definition={"type": 0},
                items=[{"title": "Holotype", "value": "Holotype"}],
            )
            self.assertTrue(resource.is_file())
            self.assertTrue(picklist.is_file())
            payload = json.loads(picklist.read_text())
            self.assertEqual(payload["items"][0]["value"], "Holotype")
            inventory = Inventory([resource_entry, picklist_entry])
            inventory.write(root)
            self.assertEqual(len(Inventory.load(root).entries), 2)

    def test_context_state_is_environment_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = ContextState(
                context="staging",
                instance="https://example.test",
                resolver_contract="specify7/current",
                resources={"viewset:x": {"version": 3, "canonicalSha256": "sha256:x"}},
            )
            path = state.write(root)
            self.assertEqual(ContextState.load(path), state)

    def test_invalid_scope_fails(self) -> None:
        with self.assertRaises(SystemExit):
            ScopeSelector(level="collection", collection="x").validate(
                source="managed"
            )


if __name__ == "__main__":
    unittest.main()
