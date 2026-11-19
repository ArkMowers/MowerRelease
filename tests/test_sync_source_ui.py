import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_source_ui as sync


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        sync.git("init", "-q", "-b", "main", str(self.source))
        self.command("config", "user.email", "test@example.invalid")
        self.command("config", "user.name", "Test")
        self.revision()
        self.main = self.revision()
        self.command("checkout", "-qb", "alpha")
        self.alpha = self.revision()

    def command(self, *args):
        return sync.git("-C", str(self.source), *args)

    def revision(self):
        self.command("commit", "-q", "--allow-empty", "-m", "revision")
        return self.command("rev-parse", "HEAD")

    def scan(self, saved, name, manual=None):
        with (
            patch.object(sync, "SOURCE", self.source.as_uri()),
            patch.object(sync, "get_cursor", side_effect=lambda _, __, b: saved.get(b)),
            patch.object(
                sync,
                "save_cursor",
                side_effect=lambda _, __, b, v: saved.update({b: v}),
            ),
        ):
            return sync.discover(None, "test", self.root / name, manual)

    def test_first_run_retries_its_start_even_if_more_commits_arrive(self):
        saved = {}
        first = self.scan(saved, "first")
        self.assertEqual(first, {"alpha": [self.alpha], "main": [self.main]})
        next_revs = [self.revision() for _ in range(8)]
        second = self.scan(saved, "second")
        self.assertEqual(second["alpha"], [self.alpha, *next_revs[:5]])
        saved.update(alpha=second["alpha"][-1], main=self.main)
        self.assertEqual(self.scan(saved, "third"), {"alpha": next_revs[5:]})
        saved["alpha"] = next_revs[-1]
        self.assertEqual(self.scan(saved, "unchanged"), {})
        self.assertFalse((self.root / "unchanged").exists())

    def test_force_push_resumes_from_shared_first_parent(self):
        saved = {"alpha": self.alpha, "main": self.main}
        self.command("branch", "previous", self.alpha)
        self.command("reset", "--hard", self.main)
        (self.source / "different").write_text("changed")
        self.command("add", ".")
        rewritten = self.revision()
        self.assertEqual(self.scan(saved, "rewritten"), {"alpha": [rewritten]})

    def test_manual_revision_never_advances_tracking(self):
        saved = {}
        self.assertEqual(self.scan(saved, "manual", self.main), {"manual": [self.main]})
        self.assertEqual(saved, {})
        with self.assertRaises(ValueError):
            self.scan(saved, "invalid", "0" * 40)

    def test_failed_publication_retries_gap_but_other_branch_advances(self):
        revs = [f"{i:040x}" for i in range(1, 5)]
        for rev in revs:
            folder = self.root / f"relay-ui-{rev}"
            folder.mkdir()
            (folder / "relay-ui.json").write_text("{}")

        def upload(path, *_):
            if path.name.endswith(revs[1]):
                raise ValueError("upload failed")

        s3 = Mock()
        with (
            patch.object(sync, "publish", side_effect=upload),
            self.assertRaisesRegex(RuntimeError, "upload failed"),
        ):
            sync.finish(s3, "test", {"alpha": revs[:3], "main": revs[3:]}, self.root)
        written = [
            (c.kwargs["Key"], json.loads(c.kwargs["Body"]))
            for c in s3.put_object.call_args_list
        ]
        self.assertEqual(
            written,
            [(sync.cursor_key("alpha"), revs[0]), (sync.cursor_key("main"), revs[3])],
        )

    def test_missing_artifact_is_not_a_legacy_build(self):
        rev = self.main
        s3 = Mock()
        with self.assertRaisesRegex(RuntimeError, "Missing successful"):
            sync.finish(s3, "test", {"alpha": [rev]}, self.root)
        s3.put_object.assert_not_called()
        folder = self.root / f"relay-ui-{rev}"
        folder.mkdir()
        (folder / "pre-relay-source").touch()
        sync.finish(s3, "test", {"manual": [rev]}, self.root)
        s3.put_object.assert_not_called()
