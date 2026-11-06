"""docker/live-deploy.sh, run against a stand-in for Docker.

The stand-in `docker` keeps no state of its own: which migrations an image
carries and whether it's healthy come from files in a folder the test makes,
and each call is logged.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "ghcr.io/eratosthene/fbc2"

STUB_DOCKER = r"""#!/bin/bash
set -u
echo "docker $*" >> "$STUB/calls"
key() { echo "$1" | tr '/:' '__'; }
running() {
    img=$(sed -n 's/^FBC_IMAGE=//p' "$REPO/docker/.env" | tail -n 1)
    echo "${img:-fbc2:latest}"
}
case "$1" in
    pull)
        case "$2" in *badpull*) exit 1 ;; esac ;;
    run)
        # docker run --rm --entrypoint sh IMAGE -c ...
        cat "$STUB/migrations/$(key "$5")" 2>/dev/null || exit 1 ;;
    image)
        # docker image inspect IMAGE
        [ -f "$STUB/migrations/$(key "$3")" ] || exit 1 ;;
    tag)
        cp "$STUB/migrations/$(key "$2")" "$STUB/migrations/$(key "$3")"
        cp "$STUB/health/$(key "$2")" "$STUB/health/$(key "$3")" ;;
    compose)
        case "$4" in
            up) running > "$STUB/up" ;;
            ps) echo cid ;;
            logs) echo "stub logs" ;;
        esac ;;
    inspect)
        img=$(cat "$STUB/up")
        case "$3" in
            *Health*) cat "$STUB/health/$(key "$img")" 2>/dev/null || echo unhealthy ;;
            *) echo running ;;
        esac ;;
esac
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A stand-in repo checkout, with `sudo` and `docker` stubbed."""
    repo = tmp_path / "repo"
    (repo / "docker").mkdir(parents=True)
    shutil.copy(ROOT / "docker/live-deploy.sh", repo / "docker/live-deploy.sh")
    (repo / "config.py").write_text("")
    # Stands in for docker/build.sh: makes fbc2:latest from the files in
    # $STUB/built (migrations, then health).
    build = repo / "docker/build.sh"
    build.write_text(
        "#!/bin/bash\n"
        'echo "build.sh" >> "$STUB/calls"\n'
        '[ -f "$STUB/built/migrations" ] || exit 1\n'
        'cp "$STUB/built/migrations" "$STUB/migrations/fbc2_latest"\n'
        'cp "$STUB/built/health" "$STUB/health/fbc2_latest"\n'
    )
    build.chmod(0o755)
    env = repo / "docker/.env"
    env.write_text("FBC_CONFIG_SECRET_KEY='keep me'\n")
    env.chmod(0o600)

    stub = tmp_path / "stub"
    for d in ("migrations", "health", "built"):
        (stub / d).mkdir(parents=True)
    (stub / "calls").write_text("")
    (stub / "up").write_text("fbc2:latest\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(STUB_DOCKER)
    (bin_dir / "sudo").write_text('#!/bin/bash\nexec "$@"\n')
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB", str(stub))
    monkeypatch.setenv("REPO", str(repo))
    monkeypatch.setenv("FBC_DEPLOY_POLL", "0")

    class Server:
        def image(self, name, migrations, health="healthy"):
            key = name.replace("/", "_").replace(":", "_")
            (stub / "migrations" / key).write_text("\n".join(migrations) + "\n")
            (stub / "health" / key).write_text(health + "\n")

        def build_result(self, migrations, health="healthy"):
            (stub / "built/migrations").write_text("\n".join(migrations) + "\n")
            (stub / "built/health").write_text(health + "\n")

        def run(self, *args):
            return subprocess.run(
                [str(repo / "docker/live-deploy.sh"), *args],
                capture_output=True,
                text=True,
            )

        def env(self):
            return env.read_text()

        def calls(self):
            return (stub / "calls").read_text()

        def previous(self):
            p = repo / "docker/.previous-image"
            return p.read_text().strip() if p.exists() else None

        def log(self):
            p = repo / "docker/deploys.log"
            return p.read_text() if p.exists() else ""

    return Server()


OLD = f"{REGISTRY}:sha-1111111"
NEW = f"{REGISTRY}:sha-2222222"


def test_deploys_a_tag_and_keeps_the_rest_of_the_env(server):
    server.image("fbc2:latest", ["a.py"])
    server.image(NEW, ["a.py"])
    result = server.run("sha-2222222")
    assert result.returncode == 0, result.stderr
    assert f"FBC_IMAGE={NEW}" in server.env()
    assert "FBC_CONFIG_SECRET_KEY='keep me'" in server.env()
    assert server.previous() == "fbc2:latest"
    assert server.log().endswith(f"{NEW} ok\n")
    assert oct(Path(os.environ["REPO"], "docker/.env").stat().st_mode)[-3:] == "600"


def test_no_arguments_shows_state_and_changes_nothing(server):
    result = server.run()
    assert result.returncode == 0
    assert "Running:  fbc2:latest (built here, not pinned)" in result.stdout
    assert "docker pull" not in server.calls()


def test_a_failed_pull_changes_nothing(server):
    result = server.run("badpull")
    assert result.returncode == 1
    assert "FBC_IMAGE" not in server.env()
    assert "up" not in server.calls().split()


def test_different_migrations_need_a_backup(server):
    server.image("fbc2:latest", ["a.py"])
    server.image(NEW, ["a.py", "b.py"])
    result = server.run("sha-2222222")
    assert result.returncode == 1
    assert "--backed-up" in result.stderr
    assert "FBC_IMAGE" not in server.env()
    assert "compose" not in server.calls()


def test_an_unreadable_running_image_needs_a_backup(server):
    server.image(NEW, ["a.py"])
    result = server.run("sha-2222222")
    assert result.returncode == 1
    assert "FBC_IMAGE" not in server.env()


def test_unhealthy_with_no_new_migrations_goes_back(server):
    server.image(OLD, ["a.py"])
    server.image(NEW, ["a.py"], health="unhealthy")
    server.run("sha-1111111", "--backed-up")
    result = server.run("sha-2222222", "--timeout", "2")
    assert result.returncode == 1
    assert f"FBC_IMAGE={OLD}" in server.env()
    assert "rolled-back" in server.log()
    assert "Back on" in result.stderr


def test_unhealthy_after_a_migration_stays_put_and_says_so(server):
    server.image(OLD, ["a.py"])
    server.image(NEW, ["a.py", "b.py"], health="unhealthy")
    server.run("sha-1111111", "--backed-up")
    result = server.run("sha-2222222", "--backed-up", "--timeout", "2")
    assert result.returncode == 1
    assert f"FBC_IMAGE={NEW}" in server.env()
    assert "Not going back automatically" in result.stderr
    assert server.previous() == OLD


def test_previous_goes_back_to_the_last_image(server):
    server.image(OLD, ["a.py"])
    server.image(NEW, ["a.py"])
    server.run("sha-1111111", "--backed-up")
    server.run("sha-2222222")
    result = server.run("--previous")
    assert result.returncode == 0, result.stderr
    assert f"FBC_IMAGE={OLD}" in server.env()
    assert server.previous() == NEW


def test_previous_with_nothing_recorded_fails(server):
    result = server.run("--previous")
    assert result.returncode == 1
    assert "no previous image" in result.stderr


def test_previous_can_be_an_image_built_here(server):
    # The first deploy records the locally built image as the previous one;
    # it isn't in a registry, so it mustn't be pulled.
    server.image("fbc2:latest", ["a.py"])
    server.image(NEW, ["a.py"])
    server.run("sha-2222222")
    result = server.run("--previous")
    assert result.returncode == 0, result.stderr
    assert "FBC_IMAGE=fbc2:latest" in server.env()
    assert "docker pull fbc2" not in server.calls()


def test_local_builds_and_runs_this_checkouts_image(server):
    server.image(NEW, ["a.py"])
    server.run("sha-2222222", "--backed-up")
    server.build_result(["a.py"])
    result = server.run("--local")
    assert result.returncode == 0, result.stderr
    assert "build.sh" in server.calls()
    assert "FBC_IMAGE=fbc2:latest" in server.env()
    assert server.previous() == NEW


def test_local_failed_build_changes_nothing(server):
    server.image(NEW, ["a.py"])
    server.run("sha-2222222", "--backed-up")
    result = server.run("--local")
    assert result.returncode == 1
    assert f"FBC_IMAGE={NEW}" in server.env()


def test_local_over_local_keeps_the_old_image_to_go_back_to(server):
    server.image("fbc2:latest", ["a.py"])
    server.build_result(["a.py"], health="unhealthy")
    result = server.run("--local", "--timeout", "2")
    assert result.returncode == 1
    assert server.previous() == "fbc2:previous"
    assert "FBC_IMAGE=fbc2:previous" in server.env()
    assert "rolled-back" in server.log()


def test_local_with_different_migrations_needs_a_backup(server):
    server.image(NEW, ["a.py"])
    server.run("sha-2222222", "--backed-up")
    server.build_result(["a.py", "b.py"])
    result = server.run("--local")
    assert result.returncode == 1
    assert "--backed-up" in result.stderr


def test_local_and_previous_conflict(server):
    assert server.run("--local", "--previous").returncode == 2


def test_the_script_is_executable():
    assert os.access(ROOT / "docker/live-deploy.sh", os.X_OK)
