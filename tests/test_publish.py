import sys
import subprocess
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
        self.assertEqual(publish.ota_sources(latest, releases, 5), [older, alpha])
        self.assertEqual(publish.ota_sources(alpha, releases, 5), [stable])

    def test_nightly_keeps_five_development_and_two_beta_ota_sources(self):
        latest = source("v4.1.6-alpha.9.g12345678")
        latest["published_at"] = "2026-09-26T18:00:00Z"
        nightlies = [source(f"v4.1.6-alpha.9.g{day:08x}") for day in range(25, 18, -1)]
        betas = [source(f"v4.1.6-alpha.{day}") for day in range(18, 15, -1)]
        for day, item in zip(range(25, 18, -1), nightlies):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        for day, item in zip(range(18, 15, -1), betas):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        self.assertEqual(
            publish.ota_sources(latest, [latest, *nightlies, *betas], 5),
            [*nightlies[:5], *betas[:2]],
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
        with patch.object(publish, "current_release", return_value=copy):
            with self.assertRaisesRegex(ValueError, "differs"):
                publish.mirror_full_release(target)

    def test_full_release_remains_available_when_no_ota_source_exists(self):
        target = source("v4.1.6-alpha.8")
        copy = mirrored(target)
        with patch.object(publish, "mirror_full_release", return_value=copy):
            self.assertIs(publish.publish_target(target, [target], 5), copy)


if __name__ == "__main__":
    unittest.main()
