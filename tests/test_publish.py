import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish


def source(tag, prerelease=True):
    name = f"arknights-mower_{tag[1:]}_windows_x64.zip"
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "published_at": "2026-09-24T18:42:41Z",
        "html_url": f"https://github.com/{publish.SOURCE_REPO}/releases/tag/{tag}",
        "body": "更新说明",
        "assets": [{"name": name, "size": 7, "digest": "sha256:" + "a" * 64}],
    }


def mirrored(target):
    item = target["assets"][0]
    return {
        "draft": False,
        "assets": [
            {
                **item,
                "browser_download_url": (
                    f"https://github.com/{publish.RELEASE_REPO}/releases/download/"
                    f"{target['tag_name']}/{item['name']}"
                ),
            }
        ],
    }


class PublishTests(unittest.TestCase):
    def test_ota_only_builds_windows_x64_and_android_arm64_and_uploads_incrementally(
        self,
    ):
        latest = source("v4.1.6-alpha.11.g12345678")
        older = source("v4.1.6-alpha.11.g87654321")
        latest["published_at"] = "2026-10-09T06:00:00Z"
        for item in (latest, older):
            item["assets"] = [
                {"name": publish.asset_name(item["tag_name"], *target), "size": 1000}
                for target in (*publish.FULL_TARGETS, ("macos", "x64", "dmg"))
            ]
        events = []

        def download(tag, artifact, directory, **kwargs):
            events.append(("download", artifact["name"], kwargs.get("repo")))
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / artifact["name"]
            path.write_bytes(b"full package")
            return path

        def build(old, new, output, **kwargs):
            events.append(("build", kwargs["platform"]))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"delta")
            return {"bytes": 5}

        def upload(command, **kwargs):
            self.assertTrue(Path(command[-1]).is_file())
            self.assertEqual(kwargs["timeout"], publish.TRANSFER_TIMEOUT)
            events.append(("upload", command[-1]))

        with (
            patch.object(publish, "mirror_full_release", return_value=mirrored(latest)),
            patch.object(publish, "download", side_effect=download),
            patch.object(publish, "build", side_effect=build),
            patch.object(publish.subprocess, "run", side_effect=upload),
            patch.object(publish, "api", return_value=mirrored(latest)),
        ):
            publish.publish_target(latest, [latest, older], 5)
        self.assertEqual(
            [e[1] for e in events if e[0] == "build"], ["windows", "android"]
        )
        self.assertEqual(
            [e[0] for e in events if e[0] in ("build", "upload")],
            ["build", "upload", "build", "upload"],
        )
        self.assertTrue(
            all(e[2] == publish.RELEASE_REPO for e in events if e[0] == "download")
        )
        self.assertNotIn(("macos", "x64", "dmg"), publish.FULL_TARGETS)
        self.assertIn(("linux", "arm64", "tar.gz"), publish.FULL_TARGETS)

    def test_invalid_target_runtime_stops_before_channel_index_write(self):
        latest = source("v4.1.6-alpha.11")
        latest["published_at"] = "2026-10-09T06:00:00Z"
        older = source("v4.1.6-alpha.10")
        with (
            patch.object(sys, "argv", ["publish.py", "--tag", latest["tag_name"]]),
            patch.object(publish, "all_releases", return_value=[latest, older]),
            patch.object(publish, "mirror_full_release", return_value=mirrored(latest)),
            patch.object(publish, "download", return_value=Path("unused.zip")),
            patch.object(Path, "unlink"),
            patch.object(
                publish, "build", side_effect=ValueError("unsafe Android runtime entry")
            ),
            patch.object(publish, "save_index") as save,
            patch.object(publish.subprocess, "run") as upload,
            self.assertRaisesRegex(ValueError, "unsafe Android runtime"),
        ):
            publish.main()
        save.assert_not_called()
        upload.assert_not_called()

    def test_legacy_source_is_skipped_but_other_build_errors_propagate(self):
        latest = source("v4.1.6-alpha.11")
        latest["published_at"] = "2026-10-09T06:00:00Z"
        older = source("v4.1.6-alpha.10")
        with (
            patch.object(publish, "mirror_full_release", return_value=mirrored(latest)),
            patch.object(publish, "download", return_value=Path("unused.zip")),
            patch.object(Path, "unlink"),
            patch.object(
                publish,
                "build",
                side_effect=publish.IncompatibleSourceError("legacy runtime"),
            ),
            patch.object(publish.subprocess, "run") as upload,
        ):
            publish.publish_target(latest, [latest, older], 5)
        upload.assert_not_called()

    def test_github_commands_and_downloads_have_finite_timeouts(self):
        import hashlib
        import tempfile

        with patch.object(
            publish.subprocess, "check_output", return_value="response"
        ) as command:
            self.assertEqual(
                publish.gh("api", "repos/ArkMowers/MowerRelease"), "response"
            )
        self.assertEqual(command.call_args.kwargs["timeout"], publish.COMMAND_TIMEOUT)
        content = b"official full package"
        artifact = {
            "name": "package.zip",
            "size": len(content),
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        }

        def download(command, **kwargs):
            (Path(command[-1]) / artifact["name"]).write_bytes(content)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(publish.subprocess, "run", side_effect=download) as transfer,
        ):
            publish.download("v4.1.6-alpha.11", artifact, Path(temporary))
        self.assertEqual(transfer.call_args.kwargs["timeout"], publish.TRANSFER_TIMEOUT)

    def test_nightly_repairs_ota_even_when_full_packages_already_exist(self):
        import yaml

        path = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/nightly-windows.yml"
        )
        workflow = yaml.safe_load(path.read_text())
        jobs = workflow["jobs"]
        select = next(
            step["run"]
            for step in jobs["prepare"]["steps"]
            if step.get("id") == "version"
        )
        self.assertLess(
            select.index('echo "tag_name=$tag_name"'), select.index("changed=false")
        )
        self.assertIn("needs.prepare.outputs.changed == 'false'", jobs["publish"]["if"])
        self.assertIn("needs.build-windows.result == 'success'", jobs["publish"]["if"])
        self.assertIn("needs.build-android.result == 'success'", jobs["publish"]["if"])
        steps = jobs["publish"]["steps"]
        ota = next(
            step
            for step in steps
            if step.get("name") == "Generate and publish OTA packages"
        )
        self.assertNotIn("if", ota)
        self.assertEqual(ota["run"], 'python -u scripts/publish.py --tag "$TAG"')
        for step in steps:
            if (
                step.get("uses") == "actions/download-artifact@v4"
                or step.get("name") == "Publish full packages"
            ):
                self.assertEqual(
                    step["if"], "${{ needs.prepare.outputs.changed == 'true' }}"
                )

    def test_resume_does_not_rebuild_an_already_uploaded_delta(self):
        latest = source("v4.1.6-alpha.11")
        latest["published_at"] = "2026-10-09T06:00:00Z"
        older = source("v4.1.6-alpha.10")
        copy = mirrored(latest)
        copy["assets"].append(
            {
                "name": publish.ota_name(
                    older["tag_name"], latest["tag_name"], "windows", "x64"
                )
            }
        )
        with (
            patch.object(publish, "mirror_full_release", return_value=copy),
            patch.object(publish, "download") as download,
            patch.object(publish, "build") as build,
        ):
            self.assertIs(publish.publish_target(latest, [latest, older], 5), copy)
        download.assert_not_called()
        build.assert_not_called()

    def test_release_listing_keeps_nightly_from_this_repository_separate(self):
        alpha = source("v4.1.6-alpha.9")
        nightly = source("v4.1.6-alpha.9.g12345678")
        nightly["html_url"] = (
            f"https://github.com/{publish.RELEASE_REPO}/releases/tag/{nightly['tag_name']}"
        )
        for item in (alpha, nightly):
            item["draft"] = False
        with patch.object(
            publish, "list_releases", side_effect=[[alpha], [nightly]]
        ) as listing:
            releases = publish.all_releases()
        self.assertEqual(
            {item["tag_name"] for item in releases},
            {alpha["tag_name"], nightly["tag_name"]},
        )
        self.assertEqual(listing.call_count, 2)

    def test_nightly_full_package_is_used_from_mowerrelease(self):
        nightly = source("v4.1.6-alpha.9.g12345678")
        copy = mirrored(nightly)
        copy["tag_name"] = nightly["tag_name"]
        with patch.object(publish, "current_release", return_value=copy):
            self.assertIs(publish.mirror_full_release(nightly), copy)

    def test_nightly_sources_include_recent_beta_without_polluting_beta_index(self):
        latest = source("v4.1.6-alpha.9.g12345678")
        older = source("v4.1.6-alpha.9.g87654321")
        alpha = source("v4.1.6-alpha.9")
        stable = source("v4.1.5", False)
        for item, day in ((latest, 26), (older, 25), (alpha, 24), (stable, 23)):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        releases = [latest, older, alpha, stable]
        self.assertEqual(publish.channel_of(latest), "dev")
        self.assertEqual(publish.channel_of(alpha), "beta")
        self.assertEqual(
            publish.ota_sources(latest, releases, 5), [older, alpha, stable]
        )
        self.assertEqual(publish.ota_sources(alpha, releases, 5), [stable])

    def test_nightly_keeps_five_development_two_beta_and_two_stable_sources(self):
        latest = source("v4.1.6-alpha.9.g12345678")
        latest["published_at"] = "2026-09-26T18:00:00Z"
        nightlies = [source(f"v4.1.6-alpha.9.g{day:08x}") for day in range(25, 18, -1)]
        betas = [source(f"v4.1.6-alpha.{day}") for day in range(18, 15, -1)]
        stables = [source(f"v4.1.{day}", False) for day in range(15, 12, -1)]
        for day, item in zip(range(25, 18, -1), nightlies):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        for day, item in zip(range(18, 15, -1), betas):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        for day, item in zip(range(15, 12, -1), stables):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        self.assertEqual(
            publish.ota_sources(latest, [latest, *nightlies, *betas, *stables], 5),
            [*nightlies[:5], *betas[:2], *stables[:2]],
        )

    def test_beta_reserves_stable_ota_source_and_filters_missing_platform(self):
        latest = source("v4.1.6-alpha.9")
        beta = source("v4.1.6-alpha.8")
        stable = source("v4.1.5", False)
        latest["published_at"] = "2026-09-26T18:00:00Z"
        beta["published_at"] = "2026-09-25T18:00:00Z"
        stable["published_at"] = "2026-09-24T18:00:00Z"
        self.assertEqual(
            publish.ota_sources(latest, [latest, beta, stable], 5),
            [beta, stable],
        )
        self.assertEqual(
            publish.ota_sources(
                latest,
                [latest, beta, stable],
                5,
                asset_target=("linux", "x64", "tar.gz"),
            ),
            [],
        )

    def test_draft_created_by_previous_run_is_found_by_release_list(self):
        draft = {"tag_name": "v4.1.6-alpha.7", "draft": True, "id": 42}
        with patch.object(
            publish,
            "api",
            side_effect=[subprocess.CalledProcessError(1, "gh"), [draft]],
        ):
            self.assertIs(publish.current_release(draft["tag_name"]), draft)

    def test_channel_index_uses_mirrored_full_asset_and_source_notes(self):
        current = source("v4.1.6-alpha.8")
        previous = source("v4.1.6-alpha.7")
        record = publish.index_record(
            current,
            mirrored(current),
            [(previous, mirrored(previous))],
        )
        self.assertEqual(record["notes"], "更新说明")
        self.assertEqual(len(record["history"]), 1)
        self.assertEqual(record["history"][0]["version"], previous["tag_name"])
        self.assertTrue(
            record["full_assets"][0]["url"].startswith(
                f"https://github.com/{publish.RELEASE_REPO}/"
            )
        )
        self.assertNotIn(publish.SOURCE_REPO, record["full_assets"][0]["url"])

    def test_existing_mirror_must_match_source_digest_and_size(self):
        target = source("v4.1.6-alpha.8")
        copy = mirrored(target)
        copy["assets"][0]["digest"] = "sha256:" + "b" * 64
        with (
            patch.object(publish, "current_release", return_value=copy),
            self.assertRaisesRegex(ValueError, "differs"),
        ):
            publish.mirror_full_release(target)

    def test_full_release_remains_available_when_no_ota_source_exists(self):
        target = source("v4.1.6-alpha.8")
        copy = mirrored(target)
        with patch.object(publish, "mirror_full_release", return_value=copy):
            self.assertIs(publish.publish_target(target, [target], 5), copy)


if __name__ == "__main__":
    unittest.main()
