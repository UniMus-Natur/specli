from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from specli.remote_v3 import (
    _exclude_dangling_vendor_registry_entry,
    _pull_vendor_resources,
)
from specli.repository_v3 import Inventory, ScopeSelector, init_repository
from specli.sync_engine import validate_repository


class DanglingVendorRegistryTests(unittest.TestCase):
    def test_exclude_helper_records_reason_and_warns(self) -> None:
        inventory = Inventory()
        scope = ScopeSelector(level="discipline-type", discipline_type="botany")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            _exclude_dangling_vendor_registry_entry(
                inventory,
                kind="AppResource",
                name="DataEntryTaskInit",
                config_dir="botany",
                filename="dataentry_task.xml",
                scope=scope,
            )
        self.assertEqual(len(inventory.entries), 1)
        entry = inventory.entries[0]
        self.assertEqual(entry.status, "excluded")
        self.assertEqual(entry.kind, "AppResource")
        self.assertEqual(entry.source, "vendor")
        self.assertIsNone(entry.path)
        self.assertEqual(
            entry.reason,
            "dangling disk registry reference (HTTP 404): "
            "/static/config/botany/dataentry_task.xml",
        )
        self.assertIn("warning: excluding vendor AppResource", stderr.getvalue())
        self.assertTrue(entry.uid.startswith("resource-excluded:"))

    def test_pull_vendor_resources_skips_404_and_keeps_present(self) -> None:
        registry = ET.fromstring(
            """<files>
            <file name="DataEntryTaskInit" file="dataentry_task.xml" mimetype="text/xml"/>
            <file name="ExpressSearchConfig" file="es_config.xml" mimetype="text/xml"/>
            </files>"""
        )

        def try_fetch(_session, _base, relpath: str) -> str | None:
            if relpath == "botany/dataentry_task.xml":
                return None
            if relpath == "botany/es_config.xml":
                return "<expressSearch/>"
            raise AssertionError(f"unexpected path {relpath!r}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repository(root, instance="https://example.test")
            inventory = Inventory()
            observed: dict = {}
            stderr = io.StringIO()
            with (
                mock.patch(
                    "specli.remote_v3._config_dirs", return_value=["botany"]
                ),
                mock.patch("specli.remote_v3._registry", return_value=registry),
                mock.patch(
                    "specli.remote_v3.try_fetch_static_config",
                    side_effect=try_fetch,
                ),
                redirect_stderr(stderr),
            ):
                _pull_vendor_resources(
                    root,
                    session=object(),
                    base="https://example.test",
                    inventory=inventory,
                    observed=observed,
                )

            statuses = {entry.status for entry in inventory.entries}
            self.assertEqual(statuses, {"excluded", "present"})
            excluded = next(e for e in inventory.entries if e.status == "excluded")
            present = next(e for e in inventory.entries if e.status == "present")
            self.assertEqual(excluded.kind, "AppResource")
            self.assertIn("dataentry_task.xml", excluded.reason or "")
            self.assertEqual(present.kind, "AppResource")
            self.assertEqual(len(observed), 1)
            self.assertIn(present.uid, observed)
            self.assertIn("warning: excluding vendor AppResource", stderr.getvalue())

            inventory.write(root)
            validate_repository(root)


if __name__ == "__main__":
    unittest.main()
