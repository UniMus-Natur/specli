from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from specli import secrets
from specli.repository_v3 import init_repository as repo_init
from specli.workspace import (
    LOCAL_SECRETS_FILE,
    find_workspace,
    load_workspace_meta,
    write_workspace_connection,
)


class RepoLocalSetupTests(unittest.TestCase):
    def test_find_workspace_walks_upward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_init(root, instance="https://example.test", user="alice")
            nested = root / "forms" / "x"
            nested.mkdir(parents=True)
            self.assertEqual(find_workspace(nested), root.resolve())

    def test_local_secrets_are_gitignored_and_chmodded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_init(root, instance="https://example.test", user="alice")
            from specli.workspace import ensure_secrets_gitignore

            ensure_secrets_gitignore(root)
            path = secrets.set_password(root, "s3cret")
            self.assertEqual(path.name, LOCAL_SECRETS_FILE)
            ignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(LOCAL_SECRETS_FILE, ignore)
            self.assertEqual(secrets.get_password(root), "s3cret")
            secrets.delete_password(root)
            self.assertIsNone(secrets.get_password(root))

    def test_connection_fields_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_init(root, instance="https://example.test")
            write_workspace_connection(
                root,
                instance="https://example.test/",
                user="bob",
                login_collection="Coll",
            )
            meta = load_workspace_meta(root)
            self.assertEqual(meta["instance"], "https://example.test")
            self.assertEqual(meta["user"], "bob")
            self.assertEqual(meta["loginCollection"], "Coll")


if __name__ == "__main__":
    unittest.main()
