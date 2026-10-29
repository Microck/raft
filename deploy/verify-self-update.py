"""Exercise self-update and the passive release notice without GitHub or npmjs.

A local HTTP server plays the GitHub releases API, the release assets and an npm
registry. Real npm and uv installs in temporary directories are upgraded to a
locally built 99.0.0 release; nothing outside those directories changes.
"""

import argparse
import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import raft  # noqa: E402

CURRENT = raft.__version__
NEXT = "99.0.0"
# These checks own the notice setting; CI may disable it for the other suites.
os.environ.pop("RAFT_NO_UPDATE_CHECK", None)


class Releases(http.server.BaseHTTPRequestHandler):
    """Serve fixed bytes per path and record every request."""

    routes = {}
    requests = []
    delay = 0

    def do_GET(self):
        Releases.requests.append(self.path)
        time.sleep(Releases.delay)
        body = Releases.routes.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


def serve_release(base, version, wheel, checksums):
    """Publish a GitHub-style release whose SHA256SUMS text is chosen by the caller."""
    tag = "v" + version
    assets = {wheel.name: wheel.read_bytes(), "SHA256SUMS": checksums.encode()}
    metadata = {
        "tag_name": tag,
        "html_url": f"{base}/releases/tag/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"{base}/assets/{tag}/{name}"} for name in assets
        ],
    }
    for name, content in assets.items():
        Releases.routes[f"/assets/{tag}/{name}"] = content
    Releases.routes["/api/latest"] = Releases.routes[f"/api/tags/{tag}"] = json.dumps(
        metadata
    ).encode()


def build(source, destination):
    """Build the wheel and npm tarball of a source tree."""
    subprocess.run(
        ["uv", "build", "-q", "--wheel", "-o", str(destination), str(source)], check=True
    )
    subprocess.run(
        ["npm", "pack", "--silent", "--ignore-scripts", "--pack-destination", str(destination)],
        cwd=source,
        check=True,
        capture_output=True,
    )
    return next(destination.glob("*.whl")), next(destination.glob("*.tgz"))


def next_release(work):
    """Build the current sources as release NEXT."""
    source = work / "next-source"
    source.mkdir()
    for name in [
        "raft_files.py",
        "raft_run.py",
        "raft_jobs.py",
        "pyproject.toml",
        "README.md",
        "LICENSE",
    ]:
        shutil.copy(ROOT / name, source)
    code = (ROOT / "raft.py").read_text()
    (source / "raft.py").write_text(code.replace(f'"{CURRENT}"', f'"{NEXT}"', 1))
    metadata = json.loads((ROOT / "package.json").read_text())
    (source / "package.json").write_text(json.dumps({**metadata, "version": NEXT}))
    return build(source, work / "next")


def installed_update(python, *arguments, env, check=False, version=None, api):
    """Run self_update inside an installed copy, as its own raft.py and sys.prefix see it."""
    program = (
        "import argparse, sys\n"
        "sys.path[:0] = sys.argv[4:]\n"
        "import raft\n"
        "sys.exit(raft.self_update(argparse.Namespace(check=sys.argv[2] == 'True', "
        "version=sys.argv[3] or None), api=sys.argv[1]))"
    )
    return subprocess.run(
        [python, "-I", "-c", program, api, str(check), version or "", *arguments],
        env=env,
        capture_output=True,
        text=True,
    )


def version_of(executable):
    return subprocess.check_output([str(executable), "--version"], text=True).strip()


def verify_versions():
    assert raft.parse_version("0.10.0") > raft.parse_version("0.9.12"), "Compared as strings"
    assert raft.parse_version("v1.2.3") == (1, 2, 3)
    assert raft.release_version("v1.2.3") == "1.2.3"
    for text in ["1.2", "1.2.3-rc1", "", "v1.2.3.4", " 1.2.3", "1.2.3\n", "latest"]:
        try:
            raft.parse_version(text)
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted release version " + repr(text))
    print("Release version parsing and numeric comparison passed", flush=True)


def verify_channels(work):
    """Refuse layouts that self-update does not own, before any network access."""
    prefix = Path(sys.prefix).resolve()
    for source, error in [
        (ROOT / "raft.py", "source checkout"),
        (work / "loose/raft.py", "neither"),
        # A project-local npm install has no PREFIX/bin/raft link to its raft.py.
        (work / "project/node_modules/@microck/raft/raft.py", "neither"),
        (work / "lib/node_modules/@microck/raft/raft.py", "neither"),
    ]:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.touch()
        try:
            raft.install_channel(source.resolve(), prefix)
        except ValueError as rejection:
            assert error in str(rejection), rejection
        else:
            raise AssertionError("self-update accepted " + str(source))
    # An editable uv tool keeps raft.py outside its environment.
    editable = work / "tools/raft"
    editable.mkdir(parents=True)
    (editable / "uv-receipt.toml").touch()
    try:
        raft.install_channel((work / "loose/raft.py").resolve(), editable.resolve())
    except ValueError:
        pass
    else:
        raise AssertionError("self-update accepted an editable uv tool")
    print("Checkout, unknown, project-local npm and editable uv layouts are refused", flush=True)


def verify_uv(work, base, current_wheel, next_wheel):
    tools, binaries = work / "uv-tools", work / "uv-bin"
    env = {**os.environ, "UV_TOOL_DIR": str(tools), "UV_TOOL_BIN_DIR": str(binaries)}
    subprocess.run(["uv", "tool", "install", "-q", str(current_wheel)], env=env, check=True)
    python, executable = tools / "raft/bin/python", tools / "raft/bin/raft"
    digest = hashlib.sha256(next_wheel.read_bytes()).hexdigest()
    for checksums, error in [
        (f"{'0' * 64}  {next_wheel.name}\n", "does not match"),
        (f"{digest}  raft-{NEXT}.tar.gz\n", "does not match"),
    ]:
        serve_release(base, NEXT, next_wheel, checksums)
        rejected = installed_update(python, env=env, api=base + "/api")
        assert rejected.returncode == 1 and error in rejected.stderr, rejected
        assert version_of(executable) == CURRENT, "A failed verification changed the install"
    serve_release(base, NEXT, next_wheel, f"{digest}  {next_wheel.name}\n")
    # uv would install into its default tool directory; refuse instead of moving.
    moved = {key: value for key, value in env.items() if key != "UV_TOOL_DIR"}
    rejected = installed_update(python, env=moved, api=base + "/api")
    assert rejected.returncode == 1 and "UV_TOOL_DIR" in rejected.stderr, rejected
    updated = installed_update(python, env=env, api=base + "/api")
    assert updated.returncode == 0, updated
    assert updated.stdout.splitlines()[0] == f"raft {CURRENT} -> {NEXT}", updated.stdout
    assert "raft doctor" in updated.stdout and "Verified" in updated.stderr, updated
    assert version_of(executable) == NEXT and version_of(binaries / "raft") == NEXT
    current = installed_update(python, env=env, api=base + "/api")
    assert current.returncode == 0 and "up to date" in current.stdout, current
    print("uv tool update, SHA256SUMS refusals and tool-directory guard passed", flush=True)


def verify_npm(work, base, current_package, next_package):
    prefix = work / "npm"
    env = {
        **os.environ,
        "npm_config_registry": base + "/",
        "npm_config_cache": str(work / "npm-cache"),
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_update_notifier": "false",
    }
    if sys.platform != "linux":
        # The package declares os: linux; allow developer runs on other systems.
        env["npm_config_force"] = "true"
    subprocess.run(
        ["npm", "install", "--global", "--prefix", str(prefix), str(current_package)],
        env=env,
        check=True,
        capture_output=True,
    )
    content = next_package.read_bytes()
    manifest = json.loads((ROOT / "package.json").read_text())
    tarball = f"{base}/npm/{next_package.name}"
    Releases.routes["/npm/" + next_package.name] = content
    Releases.routes["/@microck%2fraft"] = json.dumps(
        {
            "name": manifest["name"],
            "dist-tags": {"latest": NEXT},
            "versions": {
                NEXT: {
                    **manifest,
                    "version": NEXT,
                    "dist": {
                        "tarball": tarball,
                        "shasum": hashlib.sha1(content).hexdigest(),
                        "integrity": "sha512-"
                        + base64.b64encode(hashlib.sha512(content).digest()).decode(),
                    },
                }
            },
        }
    ).encode()
    serve_release(base, NEXT, work / "next" / f"raft-{NEXT}-py3-none-any.whl", "")
    package = prefix / "lib/node_modules/@microck/raft"
    updated = installed_update(sys.executable, str(package), env=env, api=base + "/api")
    assert updated.returncode == 0, updated
    assert updated.stdout.splitlines()[0] == f"raft {CURRENT} -> {NEXT}", updated.stdout
    assert version_of(prefix / "bin/raft") == NEXT
    assert any(path.startswith("/@microck") for path in Releases.requests), "npm skipped registry"
    print("npm global update through the registry passed", flush=True)


def verify_check(base, next_wheel):
    with tempfile.TemporaryDirectory() as cache:
        os.environ["XDG_CACHE_HOME"] = cache
        for latest, status, text in [
            (NEXT, raft.UPDATE_AVAILABLE, f"raft {NEXT} is available (you have {CURRENT})"),
            (CURRENT, 0, "up to date"),
        ]:
            serve_release(base, latest, next_wheel, "")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = raft.self_update(
                    argparse.Namespace(check=True, version=None), api=base + "/api"
                )
            assert result == status and text in output.getvalue(), (result, output.getvalue())
        try:
            raft.self_update(argparse.Namespace(check=False, version="98.0.0"), api=base + "/api")
        except ValueError as error:
            # The checkout refusal comes first; it proves no download was attempted.
            assert "source checkout" in str(error), error
        else:
            raise AssertionError("self-update ran from a source checkout")
    print("self-update --check exit status passed", flush=True)


def notice(action, api):
    """Run one passive check and return (stderr text, seconds)."""
    stderr = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stderr(stderr):
        raft.update_check(action, api=api)()
    return stderr.getvalue(), time.monotonic() - started


def verify_notice(work, base, next_wheel):
    serve_release(base, NEXT, next_wheel, "")
    expected = f"raft {NEXT} is available (you have {CURRENT}). Run: raft self-update\n"
    api = base + "/api"
    with tempfile.TemporaryDirectory() as cache:
        os.environ["XDG_CACHE_HOME"] = cache
        cached = Path(cache) / "raft/update.json"
        Releases.requests.clear()
        assert notice("list", api)[0] == expected
        assert Releases.requests == ["/api/latest"], Releases.requests
        # Within the interval there is neither a request nor a second notice.
        assert notice("list", api)[0] == "" and len(Releases.requests) == 1
        assert notice("self-update", api)[0] == ""
        cached.write_text("not json")
        os.environ["RAFT_NO_UPDATE_CHECK"] = "1"
        assert notice("list", api)[0] == "" and len(Releases.requests) == 1
        del os.environ["RAFT_NO_UPDATE_CHECK"]
        assert notice("list", api)[0] == expected and len(Releases.requests) == 2
        # An old check expires; an unreachable API stays silent and is not retried.
        cached.write_text(json.dumps({"checkedAt": time.time() - 25 * 3600}))
        assert notice("list", "http://127.0.0.1:9/api")[0] == ""
        assert time.time() - json.loads(cached.read_text())["checkedAt"] < 60
        assert notice("list", api)[0] == "" and len(Releases.requests) == 2
        # A hanging API costs at most the bounded wait.
        cached.unlink()
        Releases.delay = 5
        try:
            output, seconds = notice("list", api)
        finally:
            Releases.delay = 0
        assert output == "" and seconds < raft.UPDATE_CHECK_TIMEOUT + 1, (output, seconds)
    # The CLI adds the notice to stderr only; stdout stays byte-identical.
    home = work / "home"
    config = home / ".config/raft/incus.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"lab": {"ssh": "lab-host"}}))
    cache = home / ".cache/raft/update.json"
    cache.parent.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items() if key != "XDG_CACHE_HOME"}
    # main() reads RELEASES_API when it runs, so the CLI can target the local server.
    entry = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r}); import raft; "
        f"raft.RELEASES_API = {api!r}; sys.argv[0] = 'raft'; sys.exit(raft.main())"
    )

    def cli(*arguments, **extra):
        return subprocess.run(
            [sys.executable, "-c", entry, *arguments],
            env={**env, "HOME": str(home), **extra},
            capture_output=True,
            text=True,
        )

    listing = cli("locations")
    assert listing.stdout == '{"location": "lab", "default": true, "note": ""}\n', listing
    assert listing.stderr == expected, listing
    # The notice also follows a failed command, but only once per interval.
    cache.unlink()
    failed = cli("note", "missing", "x")
    assert failed.returncode == 1 and failed.stderr.endswith(expected), failed
    again = cli("note", "missing", "x")
    assert again.returncode == 1 and not again.stderr.endswith(expected), again
    for response in [
        cli("locations", RAFT_NO_UPDATE_CHECK="1"),
        cli("--version"),
        cli("self-update", "--version", CURRENT),
    ]:
        assert response.returncode == 0 and response.stderr == "", response
    print("Passive notice throttling, opt-out, failures and stderr-only output passed", flush=True)


def main():
    verify_versions()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Releases)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with tempfile.TemporaryDirectory(prefix="raft-self-update-") as directory:
            work = Path(directory).resolve()
            verify_channels(work)
            current_wheel, current_package = build(ROOT, work / "current")
            next_wheel, next_package = next_release(work)
            verify_check(base, next_wheel)
            verify_notice(work, base, next_wheel)
            verify_uv(work, base, current_wheel, next_wheel)
            verify_npm(work, base, current_package, next_package)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
