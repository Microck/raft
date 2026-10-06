"""Real Raft lifecycle verification; each run destroys only its own fixtures."""

import argparse
import errno
import json
import os
import pty
import select
import shlex
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
CLI = [sys.executable, str(ROOT / "raft.py")]
USER_AGENT = "OpenAI File Downloader, XaiImageApiFetch/1.0"
sys.path.insert(0, str(ROOT))
from raft import configuration, incus, remote, settings, transaction  # noqa: E402


def raft(*arguments, check=True, env=None):
    response = subprocess.run([*CLI, *arguments], capture_output=True, text=True, env=env)
    if check and response.returncode:
        raise RuntimeError(f"Raft {arguments!r} failed: {response.stderr.strip()}")
    return response


def execute(box, *arguments):
    return raft("exec", box, "--", *arguments).stdout.strip()


def wait_running(box):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        response = raft("exec", box, "--", "systemctl", "is-active", "docker", "ssh", check=False)
        if response.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Guest Docker and SSH did not become ready")


def terminal_session(box):
    master, slave = pty.openpty()
    process = subprocess.Popen([*CLI, "ssh", box], stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    transcript = b""
    deadline = time.monotonic() + 30
    sent = False
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.5)[0]:
                try:
                    transcript += os.read(master, 65536)
                except OSError:
                    break
                if not sent and transcript.rstrip().endswith((b"#", b"$")):
                    os.write(master, b"test -t 0 && echo raft-terminal-ok; exit\n")
                    sent = True
            if process.poll() is not None:
                break
        process.wait(timeout=5)
        assert process.returncode == 0 and b"\r\nraft-terminal-ok\r\n" in transcript, (
            transcript.decode(errors="replace")
        )
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)


def verify_peers(box, child):
    # Prove the peer SSH service is reachable from itself before testing denial.
    assert execute(child, "systemctl", "is-active", "ssh") == "active"
    nic = json.loads(execute(child, "ip", "-json", "addr", "show", "dev", "eth0"))[0]
    addresses = [
        item["local"] + ("%eth0" if item["family"] == "inet6" else "")
        for item in nic["addr_info"]
        if item["family"] in ["inet", "inet6"]
    ]
    assert any(":" in address for address in addresses), "Expected a link-local IPv6 address"
    execute(
        child,
        "python3",
        "-c",
        f"import socket\nfor host in {addresses!r}: socket.create_connection((host,22),2).close()",
    )
    assert (
        execute(
            box,
            "python3",
            "-c",
            f'import socket\nfor host in {addresses!r}:\n try: socket.create_connection((host,22),2)\n except OSError: continue\n raise SystemExit("peer reachable: " + host)\nprint("peers-blocked")',
        )
        == "peers-blocked"
    )


def desktop_session(box):
    # Check the browser transport too, before using a complete raw VNC viewer.
    websocket_proof = """const assert = require('node:assert/strict');
const socket = new WebSocket('ws://127.0.0.1:6080/websockify', {headers: {'User-Agent': process.argv[1]}});
socket.binaryType = 'arraybuffer';
let incoming = Buffer.alloc(0), wake;
const timeout = setTimeout(() => { console.error('WebSocket timeout'); process.exit(1); }, 10000);
socket.addEventListener('error', () => process.exit(1));
socket.addEventListener('message', event => {
    incoming = Buffer.concat([incoming, Buffer.from(event.data)]);
    if (wake) { const resume = wake; wake = undefined; resume(); }
});
async function read(count) {
    while (incoming.length < count) await new Promise(resolve => { wake = resolve; });
    const payload = incoming.subarray(0, count);
    incoming = incoming.subarray(count);
    return payload;
}
socket.addEventListener('open', async () => {
    try {
        assert.equal((await read(12)).toString(), 'RFB 003.008\\n');
        socket.send(Buffer.from('RFB 003.008\\n'));
        const security = await read((await read(1))[0]);
        assert.ok(security.includes(1));
        socket.send(Buffer.from([1]));
        assert.deepEqual(await read(4), Buffer.alloc(4));
        socket.send(Buffer.from([1]));
        const init = await read(24);
        assert.equal(init.readUInt16BE(0), 1280);
        assert.equal(init.readUInt16BE(2), 720);
        clearTimeout(timeout); socket.close();
        console.log('desktop-websocket-ok');
    } catch (error) { console.error(error); process.exit(1); }
});
"""
    assert execute(box, "node", "-e", websocket_proof, USER_AGENT) == "desktop-websocket-ok"
    proof = r"""import ctypes, select, socket, struct, time
s = socket.create_connection(("127.0.0.1", 5900), 5)
s.settimeout(5)
def read(count):
    payload = b""
    while len(payload) < count:
        chunk = s.recv(count - len(payload))
        assert chunk, "VNC disconnected"
        payload += chunk
    return payload
assert read(12).startswith(b"RFB ")
s.sendall(b"RFB 003.008\n")
security = read(read(1)[0])
assert 1 in security
s.sendall(b"\x01")
assert read(4) == b"\x00" * 4
s.sendall(b"\x01")
width, height = struct.unpack("!HH", read(4))
assert (width, height) == (1280, 720)
pixel_format = read(16)
read(struct.unpack("!I", read(4))[0])
s.sendall(struct.pack("!BBHi", 2, 0, 1, 0))
s.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, width, height))
def frame():
    message = read(1)
    if message == b"\x03":
        read(3)
        read(struct.unpack("!I", read(4))[0])
        return 0
    if message == b"\x02":
        return 0
    assert message == b"\x00"
    read(1)
    rectangles = struct.unpack("!H", read(2))[0]
    for _ in range(rectangles):
        x, y, w, h, encoding = struct.unpack("!HHHHi", read(12))
        assert encoding == 0
        assert len(read(w * h * (pixel_format[0] // 8))) == w * h * (pixel_format[0] // 8)
    return rectangles
assert frame() > 0
xlib = ctypes.CDLL("libX11.so.6")
xlib.XOpenDisplay.restype = ctypes.c_void_p
xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
xlib.XDefaultRootWindow.restype = ctypes.c_ulong
xlib.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
display = xlib.XOpenDisplay(b":99")
assert display
root = xlib.XDefaultRootWindow(display)
root_return, child_return = ctypes.c_ulong(), ctypes.c_ulong()
root_x, root_y, window_x, window_y, mask = [ctypes.c_int() for _ in range(5)]
# Keep the framebuffer request stream active, as a real VNC viewer does.
s.sendall(struct.pack("!BBHH", 5, 0, 321, 234))
s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, width, height))
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    assert xlib.XQueryPointer(display, root, *[ctypes.byref(v) for v in [root_return, child_return, root_x, root_y, window_x, window_y, mask]])
    if (root_x.value, root_y.value) == (321, 234):
        break
    if select.select([s], [], [], 0.05)[0]:
        frame()
        s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, width, height))
assert (root_x.value, root_y.value) == (321, 234), (root_x.value, root_y.value)
# Deliver keyboard input through VNC and inspect the receiving X event.
class KeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
                ("same_screen", ctypes.c_int)]
xlib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
    ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
xlib.XCreateSimpleWindow.restype = ctypes.c_ulong
window = xlib.XCreateSimpleWindow(display, root, 0, 0, 200, 100, 0, 0, 0)
xlib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
xlib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
xlib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
xlib.XFlush.argtypes = [ctypes.c_void_p]
xlib.XPending.argtypes = [ctypes.c_void_p]
xlib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
xlib.XSelectInput(display, window, 1)
xlib.XMapWindow(display, window)
xlib.XFlush(display)
time.sleep(0.5)  # Openbox must finish mapping before focus is valid.
xlib.XSetInputFocus(display, window, 1, 0)
xlib.XFlush(display)
time.sleep(0.2)
s.sendall(struct.pack("!BBHI", 4, 1, 0, ord("a")))
s.sendall(struct.pack("!BBHI", 4, 0, 0, ord("a")))
event = ctypes.create_string_buffer(192)
deadline = time.monotonic() + 5
received = False
while time.monotonic() < deadline:
    while xlib.XPending(display):
        xlib.XNextEvent(display, event)
        key = KeyEvent.from_buffer(event)
        if key.type == 2 and key.keycode == xlib.XKeysymToKeycode(display, ord("a")):
            received = True
    if received: break
    if select.select([s], [], [], 0.05)[0]:
        frame()
        s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, width, height))
assert received, "VNC keyboard input did not reach X server"
# Clipboard input must become an X selection, not just arrive at the socket.
clipboard = b"raft-clipboard-proof"
s.sendall(struct.pack("!B3xI", 6, len(clipboard)) + clipboard)
xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
xlib.XInternAtom.restype = ctypes.c_ulong
selection = xlib.XInternAtom(display, b"CLIPBOARD", 0)
property_atom = xlib.XInternAtom(display, b"RAFT_PROOF", 0)
xlib.XConvertSelection.argtypes = [ctypes.c_void_p] + [ctypes.c_ulong] * 5
xlib.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
 ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
xlib.XFree.argtypes = [ctypes.c_void_p]
xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
xlib.XGetSelectionOwner.restype = ctypes.c_ulong
deadline = time.monotonic() + 5
while not xlib.XGetSelectionOwner(display, selection):
    assert time.monotonic() < deadline, "VNC did not claim clipboard selection"
    if select.select([s], [], [], 0.05)[0]:
        frame()
        s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, width, height))
xlib.XConvertSelection(display, selection, 31, property_atom, window, 0)
xlib.XFlush(display)
deadline = time.monotonic() + 5
copied = b""
while time.monotonic() < deadline:
    actual_type, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
    fmt, data = ctypes.c_int(), ctypes.c_void_p()
    xlib.XGetWindowProperty(display, window, property_atom, 0, 1024, 0, 0,
        ctypes.byref(actual_type), ctypes.byref(fmt), ctypes.byref(count),
        ctypes.byref(remaining), ctypes.byref(data))
    if data:
        copied = ctypes.string_at(data, count.value)
        xlib.XFree(data)
    if copied == clipboard: break
    if select.select([s], [], [], 0.05)[0]:
        frame()
        s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, width, height))
assert copied == clipboard, ("VNC clipboard mismatch", copied)
s.close()
print("desktop-frame-and-input-ok")
"""
    assert execute(box, "python3", "-c", proof) == "desktop-frame-and-input-ok"


def browser_session(box):
    # Chromium's DOM/window protocol reports modern captions more reliably than XFetchName.
    execute(
        box,
        "python3",
        "-c",
        "from pathlib import Path; p=Path('/tmp/raft-gui-proof.html'); "
        "p.write_text('<title>raft-gui-proof</title><h1>raft-gui-proof</h1>'); p.chmod(0o644)",
    )
    job = raft(
        "exec",
        box,
        "--detach",
        "--",
        "runuser",
        "-u",
        "developer",
        "--",
        "env",
        "DISPLAY=:99",
        "chromium",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/raft-browser-profile",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-dev-shm-usage",
        "--user-agent=" + USER_AGENT,
        "file:///tmp/raft-gui-proof.html",
    ).stdout.strip()
    proof = """const assert = require('node:assert/strict');
(async () => {
    let page;
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
        try {
            const response = await fetch('http://127.0.0.1:9222/json/list', {headers: {'User-Agent': process.argv[1]}});
            page = (await response.json()).find(target => target.type === 'page' && target.url === 'file:///tmp/raft-gui-proof.html');
            if (page) break;
        } catch {}
        await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert.ok(page, 'Headed Chromium target missing');
    const socket = new WebSocket(page.webSocketDebuggerUrl, {headers: {'User-Agent': process.argv[1]}});
    const viewerProof = {id: 4, method: 'Runtime.evaluate', params: {expression:
        `JSON.stringify({connected: document.documentElement.classList.contains('noVNC_connected'),
          canvases: Array.from(document.querySelectorAll('canvas')).map(c => ({width: c.width, height: c.height}))})`}};
    const timeout = setTimeout(() => process.exit(1), 15000);
    socket.addEventListener('error', () => process.exit(1));
    socket.addEventListener('open', () => socket.send(JSON.stringify({id: 1, method: 'Runtime.evaluate', params: {expression: 'document.title'}})));
    socket.addEventListener('message', event => {
        try {
            const response = JSON.parse(event.data);
            assert.equal(response.error, undefined);
            if (response.id === 1) {
                assert.equal(response.result.result.value, 'raft-gui-proof');
                socket.send(JSON.stringify({id: 2, method: 'Browser.getWindowForTarget', params: {targetId: page.id}}));
            } else if (response.id === 2) {
                assert.ok(response.result.bounds.width > 0 && response.result.bounds.height > 0);
                socket.send(JSON.stringify({id: 3, method: 'Page.navigate', params:
                    {url: 'http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale'}}));
            } else if (response.id === 3) {
                assert.equal(response.result.errorText, undefined);
                socket.send(JSON.stringify(viewerProof));
            } else if (response.id === 4) {
                const viewer = JSON.parse(response.result.result.value);
                if (!viewer.connected) {
                    setTimeout(() => socket.send(JSON.stringify(viewerProof)), 200); return;
                }
                assert.ok(viewer.canvases.some(c => c.width === 1280 && c.height === 720));
                clearTimeout(timeout); socket.close(); console.log('developer-gui-and-novnc-ok');
            }
        } catch (error) { console.error(error); process.exit(1); }
    });
})().catch(error => { console.error(error); process.exit(1); });
"""
    try:
        assert execute(box, "node", "-e", proof, USER_AGENT) == "developer-gui-and-novnc-ok"
    except (RuntimeError, AssertionError):
        print(raft("logs", box, job, check=False).stdout, file=sys.stderr, flush=True)
        raise
    finally:
        raft("cancel", box, job)


def fetch_through_tunnel(arguments, path, timeout=15, default_port=None):
    if default_port is None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        arguments = [*arguments, "--local", str(port)]
    else:
        port = default_port
    tunnel = subprocess.Popen(
        [*CLI, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}", headers={"User-Agent": USER_AGENT}
                    ),
                    timeout=2,
                ) as response:
                    return response.read()
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
    finally:
        tunnel.terminate()
        tunnel.wait(timeout=10)


def verify_configuration():
    # Real CLI processes must reject invalid settings before host access.
    cases = [
        {},
        {"invalid:name": {"ssh": "unused"}},
        {"lab": {}},
        {"lab": {"ssh": "unused", "image": ""}},
    ]
    for invalid_configuration in cases:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / ".config/raft"
            config.mkdir(parents=True)
            (config / "incus.json").write_text(json.dumps(invalid_configuration))
            response = raft("new", check=False, env={**os.environ, "HOME": directory})
            assert response.returncode == 1 and "Traceback" not in response.stderr


def verify_arguments():
    # Invoke actual CLI processes: help and argument errors must not access a host.
    commands = "new list limits doctor gc recover info stop resume extend destroy ssh exec upload download snapshot snapshots restore fork forward desktop logs status cancel backup usage".split()
    assert raft("--help").returncode == 0
    for command in commands:
        assert raft(command, "--help").returncode == 0
    missing = "lab:rf-0000000000000000"
    for command in ["new", "resume", "extend", "fork"]:
        prefix = [command] if command == "new" else [command, missing]
        for lifetime in ["59", "2592001", "invalid"]:
            rejected = raft(*prefix, "--ttl", lifetime, check=False)
            assert rejected.returncode == 2 and "Traceback" not in rejected.stderr
    for options in [["--cpu", "0"], ["--cpu", "3"], ["--memory", "8GiB"]]:
        assert raft("new", *options, check=False).returncode == 2
    for arguments in [["exec", missing], ["recover", "missing"], ["forward", missing]]:
        assert raft(*arguments, check=False).returncode == 2
    print("CLI help and invalid sizing, TTL and required arguments passed", flush=True)


def verify_option_edges(location, box):
    assert any(json.loads(line)["box"] == box for line in raft("list").stdout.splitlines())
    reports = [json.loads(line) for line in raft("limits", "--json").stdout.splitlines()]
    assert {report["location"] for report in reports} == set(configuration())
    assert "CPU  RAM GiB" in raft("limits").stdout
    for arguments, message in [
        (["info", "invalid"], "location-qualified handle"),
        (["info", location + ":rf-0000000000000000"], "Raft box not found"),
        (["list", "--location", "missing-location"], "Unknown location"),
        (["limits", "--location", "missing-location"], "Unknown location"),
        (["new", "--location", "missing-location"], "Unknown location"),
        (["upload", box, "missing", "relative"], "must be absolute"),
        (["download", box, "relative", "missing"], "must be absolute"),
        (["snapshot", box, "../invalid"], "Invalid snapshot name"),
        (["restore", box, "../invalid"], "Invalid snapshot name"),
        (["forward", box, "--remote", "0", "--local", "8080"], "Ports must"),
        (["forward", box, "--remote", "8080", "--local", "65536"], "Ports must"),
        (["desktop", box, "--local", "0"], "Ports must"),
    ]:
        rejected = raft(*arguments, check=False)
        assert rejected.returncode == 1 and message in rejected.stderr, (arguments, rejected.stderr)
    for command in ["status", "logs", "cancel"]:
        for job, message in [
            ("invalid", "Use the job ID"),
            ("rfcmd-" + "0" * 32, "Detached job not found"),
        ]:
            rejected = raft(command, box, job, check=False)
            assert rejected.returncode == 1 and message in rejected.stderr
    print(
        f"{location}: global reports and invalid handles, paths, jobs and ports passed", flush=True
    )
    # /dev/full rejects real writes with ENOSPC without filling the controller disk.
    # OpenSSH alone returns zero here, so exercise both artifact receiving paths.
    for operation in ["download", "backup"]:
        with open("/dev/full", "wb", buffering=0) as destination:
            try:
                if operation == "download":
                    remote(
                        location,
                        ["exec", box.split(":", 1)[1], "--", "printf", "stream-proof"],
                        stdout=destination,
                    )
                else:
                    transaction(location, 'printf "%s" "$1"', "stream-proof", stdout=destination)
            except OSError as error:
                assert error.errno == errno.ENOSPC
            else:
                raise AssertionError(f"{operation}: silently accepted a failed controller write")
    print(f"{location}: download and backup streams rejected real ENOSPC writes", flush=True)


def verify_stopped_race(location, box):
    """Queue real disk operations behind the lock, then start their source first."""
    name = box.split(":", 1)[1]
    host = settings(location)["ssh"]
    command = ["ssh", "-T", "-o", "BatchMode=yes", host]
    holder_script = """import fcntl, os, subprocess, sys, time
with open('/run/lock/raft-incus.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    info = os.fstat(lock.fileno())
    print(f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}', flush=True)
    if sys.stdin.readline().strip() == 'start':
        subprocess.run(['incus', '--project', 'raft', 'start', sys.argv[1]], check=True)
        subprocess.run(['incus', '--project', 'raft', 'config', 'set', sys.argv[1],
                        f'user.raft.expires={int(time.time()) + 600}'], check=True)
"""
    queued_script = """from pathlib import Path
import sys
for line in Path('/proc/locks').read_text().splitlines():
    if '->' in line and sys.argv[1] in line.split():
        fields = line.split()
        pid = fields[fields.index('WRITE') + 1]
        try:
            arguments = (Path('/proc') / pid / 'cmdline').read_bytes().split(bytes([0]))
        except FileNotFoundError:
            continue
        if sys.argv[2].encode() in arguments:
            print('queued')
"""
    raft("resume", box, "--ttl", "600")
    wait_running(box)
    execute(box, "sh", "-c", "echo guarded > /workspace/persistent")
    raft("stop", box)
    children = []
    try:
        for operation in [
            ["snapshot", box, "blocked-race"],
            ["restore", box, "prepared"],
            ["fork", box, "--ttl", "600"],
        ]:
            with subprocess.Popen(
                [*command, shlex.join(["sudo", "-n", "python3", "-c", holder_script, name])],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as holder:
                contender = None
                try:
                    assert select.select([holder.stdout], [], [], 15)[0], (
                        "Lock holder did not become ready"
                    )
                    lock_key = holder.stdout.readline().strip()
                    assert lock_key.count(":") == 2, "Lock holder exited before acquiring the lock"
                    contender = subprocess.Popen(
                        [*CLI, *operation],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        assert contender.poll() is None, (
                            "Operation did not queue behind the lifecycle lock"
                        )
                        queued = subprocess.check_output(
                            [
                                *command,
                                shlex.join(
                                    ["sudo", "-n", "python3", "-c", queued_script, lock_key, name]
                                ),
                            ],
                            text=True,
                        )
                        if "queued" in queued:
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("No blocked lifecycle operation observed in /proc/locks")
                    holder.stdin.write("start\n")
                    holder.stdin.flush()
                    holder.wait(timeout=40)
                    assert holder.returncode == 0, holder.stderr.read()
                    output, errors = contender.communicate(timeout=40)
                    if operation[0] == "fork" and contender.returncode == 0:
                        children.append(output.strip())
                    assert contender.returncode != 0 and "Stop the source box" in errors, (
                        operation[0],
                        output,
                        errors,
                    )
                    wait_running(box)
                    assert execute(box, "cat", "/workspace/persistent") == "guarded"
                    assert "blocked-race" not in raft("snapshots", box).stdout
                    raft("stop", box)
                finally:
                    if holder.poll() is None:
                        holder.stdin.close()
                        holder.wait(timeout=15)
                    if contender and contender.poll() is None:
                        contender.terminate()
                        contender.wait(timeout=15)
        raft("restore", box, "prepared")
        print(
            f"{location}: snapshot, restore and fork rejected a source resumed while queued",
            flush=True,
        )
    finally:
        for child in children:
            raft("destroy", child)


def verify_reboot(location, box, child):
    """Reboot only an explicitly selected disposable host, then prove recovery."""
    host = settings(location)["ssh"]
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", host]
    boot_id = subprocess.check_output(
        [*command, "cat /proc/sys/kernel/random/boot_id"], text=True
    ).strip()
    raft("stop", child)
    raft("extend", box, "--ttl", "600")
    subprocess.run([*command, "sudo systemctl reboot"], capture_output=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [
                *command,
                "systemctl is-active incus raft-network.service raft-expire.timer >/dev/null "
                "&& cat /proc/sys/kernel/random/boot_id",
            ],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() != boot_id:
            break
        time.sleep(2)
    else:
        raise RuntimeError("Host did not return with a new kernel boot ID")
    timing = json.loads(
        subprocess.check_output(
            [
                *command,
                "python3 -c "
                + shlex.quote(
                    "import json,subprocess; print(json.dumps({unit: int(subprocess.check_output(['systemctl','show',unit,'-p',prop,'--value'])) for unit,prop in [('raft-network.service','ActiveEnterTimestampMonotonic'),('incus.service','ExecMainStartTimestampMonotonic')]}))"
                ),
            ],
            text=True,
        )
    )
    assert 0 < timing["raft-network.service"] < timing["incus.service"], (
        "Incus started before firewall installation completed"
    )
    assert json.loads(raft("info", child).stdout)["status"] == "Stopped"
    wait_running(box)
    assert execute(box, "cat", "/workspace/persistent") == "saved"
    assert "prepared" in raft("snapshots", box).stdout
    assert execute(box, "docker", "run", "--rm", "raft-verify") == "raft-docker-ok"
    raft("resume", child, "--ttl", "300")
    wait_running(child)
    verify_peers(box, child)
    assert execute(child, "cat", "/workspace/persistent") == "saved"
    print(
        f"{location}: real host reboot retained files, snapshots, state, Docker and peer isolation",
        flush=True,
    )


def verify(location, extended=False, restart_incus=False, reboot_host=False):
    verify_configuration()
    verify_arguments()
    fixtures = []
    print(f"{location}: allocation", flush=True)
    try:
        started = time.monotonic()
        target = [] if location == next(iter(configuration())) else ["--location", location]
        box = raft("new", *target, "--ttl", "1800").stdout.strip()
        fixtures.append(box)
        print(f"{location}: new took {time.monotonic() - started:.1f}s", flush=True)
        wait_running(box)
        inventory = json.loads(raft("info", box).stdout)
        assert (
            int(inventory["config"]["user.raft.expires"]) >= int(execute(box, "date", "+%s")) + 1750
        )
        assert inventory["expanded_config"]["limits.memory"] == "2GiB"
        assert inventory["expanded_config"]["limits.cpu"] == "1"
        assert inventory["expanded_config"].get("security.privileged", "false") == "false"
        assert any(
            json.loads(line)["box"] == box
            for line in raft("list", "--location", location).stdout.splitlines()
        )
        assert raft("new", "--location", location, "--ttl", "59", check=False).returncode != 0
        assert raft("resume", box, "--ttl", "600", check=False).returncode != 0
        assert raft("snapshot", box, "running", check=False).returncode != 0
        assert raft("fork", box, "--ttl", "600", check=False).returncode != 0
        terminal_session(box)
        verify_option_edges(location, box)
        if extended:
            subprocess.run([sys.executable, str(ROOT / "deploy/verify-tools.py"), box], check=True)
            for cpu in [1, 2]:
                for memory in ["1GiB", "2GiB", "4GiB"]:
                    sized = raft(
                        "new",
                        "--location",
                        location,
                        "--cpu",
                        str(cpu),
                        "--memory",
                        memory,
                    ).stdout.strip()
                    fixtures.append(sized)
                    assert (
                        int(json.loads(raft("info", sized).stdout)["config"]["user.raft.expires"])
                        > int(execute(sized, "date", "+%s")) + 550
                    )
                    assert execute(sized, "nproc") == str(cpu)
                    assert execute(sized, "cat", "/sys/fs/cgroup/memory.max") == str(
                        int(memory[0]) * 1024**3
                    )
                    raft("destroy", sized)
                    fixtures.remove(sized)
            print(f"{location}: developer tools and all resource combinations passed", flush=True)
        assert execute(box, "pwd") == "/workspace"
        host_machine = subprocess.check_output(
            ["ssh", "-T", "-o", "BatchMode=yes", settings(location)["ssh"], "uname -m"],
            text=True,
        ).strip()
        assert execute(box, "uname", "-m") == host_machine
        assert execute(box, "nproc") == "1"
        assert execute(box, "cat", "/sys/fs/cgroup/memory.max") == "2147483648"
        assert execute(box, "printf", "%s", "spaces $HOME `literal`") == "spaces $HOME `literal`"
        assert raft("exec", box, "--", "bash", "-lc", "exit 37", check=False).returncode == 37
        tools = "node npm pnpm bun deno python3 uv go rustc cargo java mvn gradle kotlin scala ruby php composer elixir dotnet R gcc clang cmake ninja git rg jq chromium ffmpeg docker"
        execute(
            box,
            "bash",
            "-lc",
            f'for tool in {tools}; do command -v "$tool" >/dev/null || exit 1; done',
        )
        assert execute(box, "node", "--version").startswith("v24.")
        assert execute(box, "docker", "info", "--format", "{{.Driver}}") in [
            "overlay2",
            "overlayfs",
        ]
        docker = execute(
            box,
            "bash",
            "-lc",
            'mkdir -p /workspace/docker-test; printf "FROM alpine:3.22\\nRUN echo raft-docker-ok > /proof\\nCMD [\\"cat\\",\\"/proof\\"]\\n" > /workspace/docker-test/Dockerfile; docker build -q -t raft-verify /workspace/docker-test; docker run --rm raft-verify',
        )
        assert "raft-docker-ok" in docker
        assert (
            execute(
                box,
                "python3",
                "-c",
                'import socket\nfor host in ["169.254.169.254","10.232.0.1"]:\n try:\n  socket.create_connection((host,22 if host.endswith(".1") else 80),2)\n except OSError: continue\n raise SystemExit("private destination reachable")\nprint("private-access-blocked")',
            )
            == "private-access-blocked"
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source with spaces.bin"
            target = Path(directory) / "target with spaces.bin"
            source.write_bytes(bytes(range(256)) * 128)
            raft("upload", box, str(source), "/workspace/file with spaces.bin")
            raft("download", box, "/workspace/file with spaces.bin", str(target))
            assert source.read_bytes() == target.read_bytes()
            assert (
                raft(
                    "download", box, "/workspace/missing-file", str(target), check=False
                ).returncode
                != 0
            )
            assert source.read_bytes() == target.read_bytes(), (
                "Failed download replaced existing destination"
            )
        job = raft(
            "exec", box, "--detach", "--", "bash", "-lc", "echo detached-ok; exit 23"
        ).stdout.strip()
        time.sleep(2)
        assert "ExecMainStatus=23" in raft("status", box, job).stdout
        assert "detached-ok" in raft("logs", box, job).stdout
        execute(box, "bash", "-lc", "echo saved > /workspace/persistent")
        raft("stop", box)
        assert json.loads(raft("info", box).stdout)["status"] == "Stopped"
        raft("snapshot", box, "prepared")
        verify_stopped_race(location, box)
        child = raft("fork", box, "--ttl", "1800").stdout.strip()
        fixtures.append(child)
        wait_running(child)
        assert execute(child, "cat", "/workspace/persistent") == "saved"
        raft("resume", box, "--ttl", "1800")
        wait_running(box)
        assert execute(box, "cat", "/workspace/persistent") == "saved"
        execute(box, "bash", "-lc", "echo changed > /workspace/persistent")
        raft("stop", box)
        raft("restore", box, "prepared")
        raft("resume", box, "--ttl", "1800")
        wait_running(box)
        assert execute(box, "cat", "/workspace/persistent") == "saved"
        server = raft(
            "exec", box, "--detach", "--", "python3", "-m", "http.server", "8080"
        ).stdout.strip()
        assert (
            fetch_through_tunnel(["forward", box, "--remote", "8080"], "/persistent").strip()
            == b"saved"
        )
        assert "ActiveState=active" in raft("status", box, server).stdout
        assert b"noVNC" in fetch_through_tunnel(["desktop", box], "/vnc.html", timeout=20)
        assert b"noVNC" in fetch_through_tunnel(
            ["desktop", box], "/vnc.html", timeout=20, default_port=6080
        )
        assert execute(box, "systemctl", "is-active", "raft-desktop") == "active"
        assert "noVNC" in execute(
            box,
            "curl",
            "-A",
            USER_AGENT,
            "--retry",
            "10",
            "--retry-connrefused",
            "--retry-delay",
            "1",
            "-fsS",
            "http://127.0.0.1:6080/vnc.html",
        )
        assert "raft-browser-ok" in execute(
            box,
            "chromium",
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--dump-dom",
            "data:text/html,<h1>raft-browser-ok</h1>",
        )
        desktop_session(box)
        desktop_session(box)  # A second full connection proves reconnect.
        browser_session(box)
        execute(box, "systemctl", "stop", "raft-desktop")
        verify_peers(box, child)
        raft("stop", child)
        raft("resume", child, "--ttl", "60")
        if restart_incus:
            subprocess.run(
                ["ssh", "-T", settings(location)["ssh"], "sudo -n systemctl restart incus"],
                check=True,
            )
        wait_running(box)
        assert execute(box, "cat", "/workspace/persistent") == "saved"
        print(
            f"{location}: tools, Docker, files, limits, snapshots, restore, forks, jobs, tunnels, desktop and isolation passed",
            flush=True,
        )
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if json.loads(raft("info", child).stdout)["status"] == "Stopped":
                break
            time.sleep(5)
        else:
            raise RuntimeError("Automatic host expiry did not stop the child")
        raft("resume", child, "--ttl", "300")
        assert execute(child, "cat", "/workspace/persistent") == "saved"
        print(f"{location}: scheduled TTL stop retained disk and resumed successfully", flush=True)
        incus(
            location,
            "config",
            "set",
            child.split(":", 1)[1],
            "user.raft.expires=1",
            locked=True,
        )
        raft("gc")
        assert json.loads(raft("info", child).stdout)["status"] == "Stopped"
        raft("resume", child, "--ttl", "2592000")
        assert execute(child, "cat", "/workspace/persistent") == "saved"
        assert (
            int(json.loads(raft("info", child).stdout)["config"]["user.raft.expires"])
            > int(execute(child, "date", "+%s")) + 2591950
        )
        print(
            f"{location}: manual gc retained disk and maximum TTL resumed successfully", flush=True
        )
        if reboot_host:
            verify_reboot(location, box, child)
    finally:
        for box in reversed(fixtures):
            raft("destroy", box)
        print(f"{location}: test boxes destroyed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", required=True)
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Run language programs and every resource combination",
    )
    parser.add_argument(
        "--restart-incus",
        action="store_true",
        help="Restart the host Incus daemon; use only on dedicated test hosts",
    )
    parser.add_argument(
        "--reboot-host",
        action="store_true",
        help="Reboot the selected host; use only on a disposable host with independent controller",
    )
    args = parser.parse_args()
    verify(args.location, args.extended, args.restart_incus, args.reboot_host)
