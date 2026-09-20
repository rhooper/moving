"""The version: one source of truth, semver, and bumped by committing.

Asked for as "add semver, auto-update version number on commit". The number
lives in src/movingbox/version.py alone -- pyproject reads it from there
(hatchling dynamic version), the API serves it -- and a pre-commit hook bumps
the patch on every commit that does not bump it itself.
"""

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import movingbox
from movingbox.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class TestOneSourceOfTruth:
    def test_the_version_is_semver(self):
        assert SEMVER.match(movingbox.__version__)

    def test_pyproject_reads_it_rather_than_repeating_it(self):
        pyproject = (ROOT / "pyproject.toml").read_text()

        assert 'dynamic = ["version"]' in pyproject
        assert "src/movingbox/version.py" in pyproject
        assert not re.search(r'^version = "', pyproject, re.M)

    def test_the_api_serves_it(self, config):
        app = create_app(config)
        with TestClient(app) as client:
            health = client.get("/health").json()

        assert app.version == movingbox.__version__
        assert health["version"] == movingbox.__version__


class TestTheHook:
    """The pre-commit hook itself, run by git in a scratch repository."""

    @pytest.fixture
    def repo(self, tmp_path):
        def git(*args):
            done = subprocess.run(
                ["git", *args], cwd=tmp_path, capture_output=True, text=True, timeout=60
            )
            assert done.returncode == 0, done.stderr
            return done.stdout

        git("init", "-q")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        source = tmp_path / "src" / "movingbox"
        source.mkdir(parents=True)
        (source / "version.py").write_text('__version__ = "1.2.3"\n')
        hook_dir = tmp_path / ".git" / "hooks"
        hook = hook_dir / "pre-commit"
        hook.write_text((ROOT / "scripts" / "claude" / "hooks" / "pre-commit").read_text())
        hook.chmod(0o755)
        git("add", "-A")
        git("commit", "-q", "-m", "start", "--no-verify")
        return tmp_path, git

    def version_in(self, repo_path):
        text = (repo_path / "src" / "movingbox" / "version.py").read_text()
        return re.search(r'__version__ = "([^"]+)"', text).group(1)

    def test_an_ordinary_commit_bumps_the_patch(self, repo):
        path, git = repo
        (path / "change.txt").write_text("x")
        git("add", "change.txt")

        git("commit", "-q", "-m", "a change")

        assert self.version_in(path) == "1.2.4"
        # ...and the bump is IN the commit, not left lying around.
        assert git("status", "--porcelain") == ""

    def test_two_commits_bump_twice(self, repo):
        path, git = repo
        for name in ("a", "b"):
            (path / f"{name}.txt").write_text(name)
            git("add", f"{name}.txt")
            git("commit", "-q", "-m", name)

        assert self.version_in(path) == "1.2.5"

    def test_a_commit_that_bumps_the_version_itself_is_left_alone(self, repo):
        # `make version-minor` stages 1.3.0; the hook must not make it 1.3.1.
        path, git = repo
        (path / "src" / "movingbox" / "version.py").write_text('__version__ = "1.3.0"\n')
        git("add", "src/movingbox/version.py")

        git("commit", "-q", "-m", "minor: pushbutton rows")

        assert self.version_in(path) == "1.3.0"

    @pytest.mark.parametrize("part,expected", [("minor", "1.3.0"), ("major", "2.0.0")])
    def test_a_hand_bump_then_a_commit_lands_exactly_on_the_number(self, repo, part, expected):
        # bump.sh stages version.py, and the hook sees that and stands down --
        # so minor gives x.Y+1.0, not x.Y+1.1.
        path, git = repo
        script = path / "scripts" / "claude" / "bump.sh"
        script.parent.mkdir(parents=True)
        script.write_text((ROOT / "scripts" / "claude" / "bump.sh").read_text())
        script.chmod(0o755)

        done = subprocess.run([str(script), part], cwd=path, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        git("commit", "-q", "-m", f"{part} bump")

        assert self.version_in(path) == expected
