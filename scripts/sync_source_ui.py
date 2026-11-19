"""Discover official source revisions and checkpoint completed R2 publications."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from publish_relay_ui import publish, r2_client

SOURCE = "https://github.com/ArkMowers/arknights-mower.git"
BRANCHES = ("alpha", "main")
BATCH = 6
SHA = re.compile(r"[0-9a-f]{40}")


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def cursor_key(branch):
    if branch not in BRANCHES:
        raise ValueError("Unknown source branch")
    return f"_sync/source-v1/{branch}.json"


def get_cursor(s3, bucket, branch):
    from botocore.exceptions import ClientError

    try:
        item = s3.get_object(Bucket=bucket, Key=cursor_key(branch))
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
            return None
        raise
    value = json.loads(item["Body"].read())
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("Invalid saved revision")
    return value


def save_cursor(s3, bucket, branch, revision):
    if not SHA.fullmatch(revision):
        raise ValueError("Invalid checkpoint revision")
    s3.put_object(
        Bucket=bucket,
        Key=cursor_key(branch),
        Body=json.dumps(revision).encode(),
        ContentType="application/json",
        CacheControl="no-store",
    )


def pending(repo, tip, cursor):
    if cursor == tip:
        return []
    history = git("-C", str(repo), "rev-list", "--first-parent", tip).splitlines()
    if cursor in history:
        return list(reversed(history[: history.index(cursor)]))[:BATCH]
    # On a rewritten branch resume after the newest shared first-parent commit.
    known = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--first-parent", cursor],
        text=True,
        capture_output=True,
        check=False,
    )
    if known.returncode:
        raise ValueError(
            f"Saved revision {cursor} is unavailable; inspect rewritten history"
        )
    old = set(known.stdout.splitlines())
    shared = next((i for i, commit in enumerate(history) if commit in old), None)
    if shared is None:
        raise ValueError("Source branch has no shared history with its checkpoint")
    return list(reversed(history[:shared]))[:BATCH] or [tip]


def discover(s3, bucket, repo, manual):
    heads = {
        ref.rsplit("/", 1)[-1]: rev
        for rev, ref in (
            line.split()
            for line in git("ls-remote", "--heads", SOURCE, *BRANCHES).splitlines()
        )
    }
    cursors = {} if manual else {b: get_cursor(s3, bucket, b) for b in BRANCHES}
    if not manual and heads == cursors:
        return {}
    git("init", "--bare", str(repo))
    git("-C", str(repo), "remote", "add", "origin", SOURCE)
    git(
        "-C",
        str(repo),
        "fetch",
        "--filter=tree:0",
        "--no-tags",
        "origin",
        *(f"+refs/heads/{b}:refs/heads/{b}" for b in BRANCHES),
    )
    if manual:
        if not SHA.fullmatch(manual) or manual not in (
            git("-C", str(repo), "rev-list", "--branches").splitlines()
        ):
            raise ValueError("Manual revision must belong to an official branch")
        return {"manual": [manual]}
    plan = {}
    for branch in BRANCHES:
        tip = git("-C", str(repo), "rev-parse", branch)
        cursor = cursors[branch]
        if cursor and cursor != tip:
            if subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", cursor], capture_output=True
            ).returncode:
                git(
                    "-C",
                    str(repo),
                    "fetch",
                    "--filter=tree:0",
                    "--no-tags",
                    "origin",
                    cursor,
                )
        if cursor is None:
            # Persist the starting point before building so a failed first run is retried.
            cursor = git("-C", str(repo), "rev-parse", tip + "^1")
            save_cursor(s3, bucket, branch, cursor)
        revisions = pending(repo, tip, cursor)
        if revisions:
            plan[branch] = revisions
    return plan


def finish(s3, bucket, plan, artifacts):
    failures = []
    complete = set()
    for branch, revisions in plan.items():
        for revision in revisions:
            if not SHA.fullmatch(revision):
                raise ValueError("Invalid planned revision")
            root = artifacts / f"relay-ui-{revision}"
            try:
                if revision not in complete:
                    if (root / "relay-ui.json").is_file():
                        publish(root, bucket, s3)
                    elif not (root / "pre-relay-source").is_file():
                        raise ValueError("Missing successful build artifact")
                    complete.add(revision)
                if branch != "manual":
                    save_cursor(s3, bucket, branch, revision)
            except Exception as exc:
                failures.append(f"{branch} {revision}: {exc}")
                break
    if failures:
        raise RuntimeError("\n".join(failures))


if __name__ == "__main__":
    client = r2_client()
    bucket = os.environ["R2_BUCKET"]
    if sys.argv[1] == "discover":
        plan = discover(
            client,
            bucket,
            Path(os.environ["RUNNER_TEMP"]) / "source.git",
            os.getenv("REVISION"),
        )
        revisions = sorted({rev for branch in plan.values() for rev in branch})
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("plan=" + json.dumps(plan, separators=(",", ":")), file=output)
            print("revisions=" + json.dumps(revisions), file=output)
    elif sys.argv[1] == "finish":
        finish(client, bucket, json.loads(os.environ["PLAN"]), Path("artifacts"))
    else:
        raise SystemExit("Unknown sync operation")
