import re
import subprocess

from pathlib import Path
from curl_cffi.requests import get

GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")


class Update:
    def __init__(self):
        self.root_dir: Path = Path.cwd().parent
        self.api_url: str | None = self._github_api_url()

    def _github_api_url(self) -> str | None:
        try:
            remote_url = subprocess.check_output(
                ("git", "remote", "get-url", "origin"),
                cwd=self.root_dir,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            return None

        match = GITHUB_REMOTE_RE.search(remote_url)
        if not match:
            return None

        return f"https://api.github.com/repos/{match['owner']}/{match['repo']}/commits/master"

    def local_hash(self) -> str:
        return subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.root_dir, text=True).strip()

    def remote_hash(self) -> str | None:
        if self.api_url is None:
            return None

        resp = get(self.api_url, headers={"User-Agent": "update-check"})
        if not resp.ok:
            return None

        data = resp.json()
        return data["sha"]

    def available(self) -> bool:
        remote_hash = self.remote_hash()
        return remote_hash is not None and self.local_hash() != remote_hash

    def pull_latest(self) -> bool:
        return (
            subprocess.run(
                ("git", "pull", "--ff-only", "origin", "master"),
                cwd=self.root_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
