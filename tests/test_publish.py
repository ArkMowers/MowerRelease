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
    def test_nightly_is_separate_from_beta_and_only_uses_nightly_ota_bases(self):
        latest = source("v4.1.6-alpha.9.g12345678")
        older = source("v4.1.6-alpha.9.g87654321")
        alpha = source("v4.1.6-alpha.9")
        stable = source("v4.1.5", False)
        for item, day in ((latest, 26), (older, 25), (alpha, 24), (stable, 23)):
            item["published_at"] = f"2026-09-{day}T18:00:00Z"
        releases = [latest, older, alpha, stable]
        self.assertEqual(publish.channel_of(latest), "dev")
        self.assertEqual(publish.channel_of(alpha), "beta")
        self.assertEqual(publish.ota_sources(latest, releases, 5), [older])
        self.assertEqual(publish.ota_sources(alpha, releases, 5), [stable])

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
