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

    def scan(self, saved, name, manual=None, reviewed_custom=False):
        with (
            patch.object(sync, "SOURCE", self.source.as_uri()),
            patch.object(sync, "get_cursor", side_effect=lambda _, __, b: saved.get(b)),
            patch.object(
                sync,
                "save_cursor",
                side_effect=lambda _, __, b, v: saved.update({b: v}),
            ),
        ):
            return sync.discover(
                None, "test", self.root / name, manual, reviewed_custom
            )

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

    def test_reviewed_custom_requires_exact_sha_and_leaves_cursors_untouched(self):
        self.command("checkout", "-qb", "custom")
        (self.source / "custom").touch()
        self.command("add", "custom")
        custom = self.revision()
        saved = {}
        with self.assertRaisesRegex(ValueError, "official branch"):
            self.scan(saved, "default", custom)
        self.assertEqual(
            self.scan(saved, "reviewed", custom, True), {"manual": [custom]}
        )
        for invalid in (None, "custom", custom[:7]):
            with self.assertRaisesRegex(ValueError, "full commit SHA"):
                self.scan(saved, "invalid", invalid, True)
        self.assertEqual(saved, {})

    def identify(self, revision, build):
        path = self.root / f"relay-id-{revision}"
        path.mkdir()
        (path / "build-id").write_text(build)

    def built(self, build, actual=None):
        path = self.root / f"relay-ui-{build}"
        path.mkdir()
        (path / "relay-ui.json").write_text(json.dumps({"build": actual or build}))

    def test_cached_and_shared_builds_are_selected_and_checkpointed_once(self):
        revs = [f"{i:040x}" for i in range(5)]
        cached, shared = "a" * 64, "b" * 64
        for rev, build in zip(
            revs, (cached, shared, shared, "pre-relay-source", cached)
        ):
            self.identify(rev, build)
        s3 = Mock()
        with patch.object(
            sync, "manifest_exists", side_effect=lambda s, b, k: k == cached
        ) as head:
            self.assertEqual(
                sync.needed_builds(s3, "bucket", revs, self.root),
                [{"revision": revs[1], "build": shared}],
            )
            self.assertEqual(head.call_count, 2)
            self.built(shared)
            with patch.object(sync, "publish") as publish:
                sync.finish(
                    s3, "bucket", {"alpha": revs[:2], "main": revs[2:]}, self.root
                )
                publish.assert_called_once_with(
                    self.root / f"relay-ui-{shared}", "bucket", s3
                )
        self.assertEqual(
            [json.loads(c.kwargs["Body"]) for c in s3.put_object.call_args_list], revs
        )

    def test_entirely_cached_manual_run_skips_build_and_cursor(self):
        self.identify(self.alpha, "a" * 64)
        s3 = Mock()
        with (
            patch.object(sync, "manifest_exists", return_value=True),
            patch.object(sync, "publish") as publish,
        ):
            self.assertEqual(
                sync.needed_builds(s3, "bucket", [self.alpha], self.root), []
            )
            sync.finish(s3, "bucket", {"manual": [self.alpha]}, self.root)
            publish.assert_not_called()
        s3.put_object.assert_not_called()

    def test_preflight_gap_or_disappeared_manifest_does_not_advance_cursor(self):
        s3 = Mock()
        with self.assertRaisesRegex(RuntimeError, "build-id"):
            sync.finish(s3, "test", {"alpha": [self.alpha]}, self.root)
        build = "a" * 64
        self.identify(self.alpha, build)
        with (
            patch.object(sync, "manifest_exists", return_value=False),
            self.assertRaisesRegex(RuntimeError, "Missing successful"),
        ):
            sync.finish(s3, "test", {"alpha": [self.alpha]}, self.root)
        self.built(build, actual="b" * 64)
        with patch.object(sync, "publish") as publish:
            with self.assertRaisesRegex(RuntimeError, "differs"):
                sync.finish(s3, "test", {"alpha": [self.alpha]}, self.root)
            publish.assert_not_called()
        s3.put_object.assert_not_called()

    def test_failed_publication_retries_gap_but_other_branch_advances(self):
        revs = [f"{i:040x}" for i in range(1, 5)]
        for rev in revs:
            build = rev.rjust(64, "0")
            self.identify(rev, build)
            self.built(build)

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
