"""Check capacity boundaries and optionally real disposable box transitions."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import capacity  # noqa: E402

GIB = 1024**3
CLI = [sys.executable, str(ROOT / "raft.py")]


def policy_checks():
    # These are allocation fixtures, not mocked host services. Expected counts
    # describe the documented policy independently of its arithmetic.
    host = {
        "cpus": 8,
        "memoryTotalBytes": 16 * GIB,
        "memoryAvailableBytes": 14 * GIB,
        "hostDiskFreeBytes": 40 * GIB,
        "poolTotalBytes": 60 * GIB,
        "poolFreeBytes": 30 * GIB,
    }
    running = {"status": "Running", "expanded_config": {"limits.cpu": "2", "limits.memory": "4GiB"}}
    stopped = {"status": "Stopped"}
    report = capacity(host, [running, stopped])
    assert report["savedBoxes"] == 2 and report["activeBoxes"] == 1
    assert report["allocated"] == {"cpus": 2, "memoryBytes": 4 * GIB}
    size = next(r for r in report["recommendations"] if r["cpu"] == 2 and r["memoryGiB"] == 4)
    assert (size["totalRunning"], size["additionalRunning"], size["newBoxes"]) == (3, 2, 2)
    full = capacity(host, [stopped] * 4)
    assert full["savedSlots"] == 0
    assert all(r["newBoxes"] == 0 for r in full["recommendations"])
    assert any(r["additionalRunning"] > 0 for r in full["recommendations"])
    frozen = capacity(host, [{**running, "status": "Frozen"}])
    assert frozen["allocated"] == report["allocated"]
    low = capacity({**host, "memoryAvailableBytes": GIB, "poolFreeBytes": GIB}, [])
    assert all(r["additionalRunning"] == 0 for r in low["recommendations"])
    assert len(low["warnings"]) == 2
    assert all(r["totalRunning"] == 0 for r in capacity({**host, "cpus": 1}, [])["recommendations"])
    assert all(
        r["totalRunning"] == 0
        for r in capacity({**host, "memoryTotalBytes": GIB}, [])["recommendations"]
    )
    large = capacity({**host, "cpus": 64, "memoryTotalBytes": 128 * GIB}, [])
    assert large["reserve"]["memoryBytes"] == 128 * GIB // 10
    assert all(r["totalRunning"] == 4 for r in large["recommendations"])
    over = capacity({**host, "cpus": 2}, [running])
    assert over["warnings"] and all(r["additionalRunning"] == 0 for r in over["recommendations"])
    for cpu, memory, expected in [(2, 8, 1), (4, 8, 3), (4, 16, 3), (8, 32, 4)]:
        example = capacity({**host, "cpus": cpu, "memoryTotalBytes": memory * GIB}, [])
        default = next(
            r for r in example["recommendations"] if r["cpu"] == 1 and r["memoryGiB"] == 2
        )
        assert default["totalRunning"] == expected
    print(
        "Capacity policy: mixed sizes, stopped/frozen boxes, low RAM/disk, reserves and saved cap passed",
        flush=True,
    )


def command(*args):
    return subprocess.check_output([*CLI, *args], text=True).strip()


def live_checks(location):
    def limits():
        return json.loads(command("limits", "--location", location, "--json"))

    baseline = limits()
    if baseline["savedSlots"] == 0:
        raise RuntimeError("Capacity E2E needs one free saved-box slot")
    box = command("new", "--location", location, "--cpu", "1", "--memory", "1GiB", "--ttl", "600")
    try:
        active = limits()
        assert active["savedBoxes"] == baseline["savedBoxes"] + 1
        assert active["activeBoxes"] == baseline["activeBoxes"] + 1
        assert active["allocated"]["cpus"] == baseline["allocated"]["cpus"] + 1
        assert active["allocated"]["memoryBytes"] == baseline["allocated"]["memoryBytes"] + GIB
        assert active["savedSlots"] == baseline["savedSlots"] - 1
        command("stop", box)
        stopped = limits()
        assert stopped["savedBoxes"] == active["savedBoxes"]
        assert stopped["activeBoxes"] == baseline["activeBoxes"]
        assert stopped["allocated"] == baseline["allocated"]
        command("resume", box, "--ttl", "600")
        resumed = limits()
        assert resumed["activeBoxes"] == active["activeBoxes"]
        assert resumed["allocated"] == active["allocated"]
        assert "CPU  RAM GiB" in command("limits", "--location", location)
    finally:
        command("destroy", box)
    final = limits()
    assert final["savedBoxes"] == baseline["savedBoxes"]
    assert final["activeBoxes"] == baseline["activeBoxes"]
    assert final["allocated"] == baseline["allocated"]
    print(f"{location}: real capacity create/stop/resume/destroy reporting passed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", help="Also test a real host using one disposable box")
    args = parser.parse_args()
    policy_checks()
    if args.location:
        live_checks(args.location)
