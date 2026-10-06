"""Measure real cached-image creation, resume, Docker and on-demand desktop latency."""

import argparse
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import configuration, incus  # noqa: E402

spec = importlib.util.spec_from_file_location("verify_live", ROOT / "deploy/verify-live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

DOCKER_READY = """import subprocess,time
end=time.monotonic()+60
while time.monotonic()<end:
 result=subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,text=True)
 if result.returncode==0:
  print(result.stdout.strip());break
 time.sleep(0.1)
else: raise SystemExit('Docker did not become ready within 60 seconds')
"""
DESKTOP_READY = """import socket,time,urllib.request
end=time.monotonic()+30
while time.monotonic()<end:
 try:
  request=urllib.request.Request('http://127.0.0.1:6080/vnc.html',headers={'User-Agent':'OpenAI File Downloader, XaiImageApiFetch/1.0'})
  with urllib.request.urlopen(request,timeout=1) as page: assert page.status==200
  with socket.create_connection(('127.0.0.1',5900),1) as vnc: assert vnc.recv(12).startswith(b'RFB ')
  print('desktop-ready');break
 except (OSError,AssertionError): time.sleep(0.1)
else: raise SystemExit('Desktop HTTP and VNC did not become ready within 30 seconds')
"""


def readiness(box, started, returned, desktop):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        probe = live.raft("exec", box, "--", "true", check=False)
        if probe.returncode == 0:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Guest command execution did not become ready")
    measured = {
        "cliReturnSeconds": returned - started,
        "commandReadySeconds": time.monotonic() - started,
    }
    live.execute(box, "python3", "-c", DOCKER_READY)
    measured["dockerReadySeconds"] = time.monotonic() - started
    if desktop:
        desktop_started = time.monotonic()
        live.execute(box, "systemctl", "start", "raft-desktop")
        live.execute(box, "python3", "-c", DESKTOP_READY)
        measured["desktopStartSeconds"] = time.monotonic() - desktop_started
    return {name: round(value, 3) for name, value in measured.items()}


def benchmark(location, samples, desktop):
    host = configuration()[location]["ssh"]
    metadata = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", host, "uname -m; nproc"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    started = time.monotonic()
    subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", host, "true"], check=True)
    report = {
        "hostArchitecture": metadata[0],
        "hostCpus": int(metadata[1]),
        "guestCpus": 1,
        "guestMemory": "2GiB",
        "sshRoundTripSeconds": round(time.monotonic() - started, 3),
        "scope": "Cached local image; includes controller CLI and SSH; no host reboot or download",
        "samples": [],
    }
    for index in range(samples):
        box = None
        try:
            started = time.monotonic()
            box = live.raft("new", "--location", location, "--ttl", "1800").stdout.strip()
            returned = time.monotonic()
            created = readiness(box, started, returned, desktop)
            if index == 0:
                report["guestArchitecture"] = live.execute(box, "dpkg", "--print-architecture")
                report["bootBlame"] = live.execute(box, "systemd-analyze", "blame")
                report["bootCriticalChain"] = live.execute(box, "systemd-analyze", "critical-chain")
                report["imageSizeBytes"] = json.loads(
                    incus(
                        location,
                        "image",
                        "list",
                        configuration()[location]["image"],
                        "--format",
                        "json",
                    )
                )[0]["size"]
            live.raft("stop", box)
            started = time.monotonic()
            live.raft("resume", box, "--ttl", "1800")
            returned = time.monotonic()
            resumed = readiness(box, started, returned, desktop)
            sample = {"create": created, "resume": resumed}
            report["samples"].append(sample)
            print(json.dumps({"sample": index + 1, **sample}), file=sys.stderr, flush=True)
        finally:
            if box is not None:
                live.raft("destroy", box)
    report["summary"] = {}
    for action in ("create", "resume"):
        report["summary"][action] = {}
        for metric in report["samples"][0][action]:
            values = sorted(sample[action][metric] for sample in report["samples"])
            report["summary"][action][metric] = {
                "median": round(statistics.median(values), 3),
                "min": values[0],
                "max": values[-1],
                "p95": values[math.ceil(0.95 * len(values)) - 1],
            }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--desktop", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.samples <= 20:
        parser.error("samples must be between 1 and 20")
    print(json.dumps(benchmark(args.location, args.samples, args.desktop), indent=2))


if __name__ == "__main__":
    main()
