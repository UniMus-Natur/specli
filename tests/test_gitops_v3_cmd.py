from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specli.commands.gitops_v3_cmd import _assert_no_local_edits
from specli.repository_v3 import (
    Inventory,
    InventoryEntry,
    ScopeSelector,
    init_repository,
)


class PullBootstrapTests(unittest.TestCase):
    def test_first_pull_accepts_empty_initialized_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repository(root, instance="https://example.test")
            _assert_no_local_edits(root, "default")

    def test_missing_state_rejects_nonempty_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repository(root, instance="https://example.test")
            Inventory(
                [
                    InventoryEntry(
                        uid="resource:excluded",
                        kind="AppResource",
                        source="managed",
                        status="excluded",
                        path=None,
                        scope=ScopeSelector(level="common"),
                        reason="test",
                    )
                ]
            ).write(root)
            with self.assertRaises(SystemExit):
                _assert_no_local_edits(root, "default")


if __name__ == "__main__":
    unittest.main()
