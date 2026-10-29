"""JSONL output transport and parser-driven shell completion."""

import argparse
import base64
import json
import os
import select
import sys
import threading

_active = None


class UsageError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, allow_abbrev=False)

    def error(self, message):
        if _active is None:
            super().error(message)
        raise UsageError(message)


class JsonOutput:
    """Capture OS descriptors too: subprocess bytes must not corrupt JSON stdout."""

    def start(self):
        global _active
        sys.stdout.flush()
        sys.stderr.flush()
        self.output = os.dup(1)
        self.saved = {fd: os.dup(fd) for fd in (1, 2)}
        self.readers = {}
        self.lock = threading.Lock()
        self.closed = False
        for fd, kind in ((1, "stdout"), (2, "stderr")):
            reader, writer = os.pipe()
            self.readers[reader] = kind
            os.dup2(writer, fd)
            os.close(writer)
        _active = self
        self.thread = threading.Thread(target=self.drain, daemon=True)
        self.thread.start()
        return self

    def event(self, kind, **fields):
        if self.closed:
            return
        encoded = (json.dumps({"type": kind, **fields}) + "\n").encode()
        with self.lock:
            if self.closed:
                return
            try:
                while encoded:
                    encoded = encoded[os.write(self.output, encoded) :]
            except BrokenPipeError:
                # Continue draining so the command cannot deadlock on a full pipe
                # after its consumer exits. No output can be delivered anymore.
                self.closed = True

    def drain(self):
        readers = self.readers
        while readers:
            ready, _, _ = select.select(readers, [], [])
            for fd in ready:
                chunk = os.read(fd, 16384)
                if chunk and not self.closed:
                    self.event(
                        readers[fd], encoding="base64", data=base64.b64encode(chunk).decode()
                    )
                elif not chunk:
                    del readers[fd]
                    os.close(fd)

    def finish(self, status):
        global _active
        sys.stdout.flush()
        sys.stderr.flush()
        for fd, original in self.saved.items():
            os.dup2(original, fd)
            os.close(original)
        self.thread.join()
        self.event("exit", code=status)
        os.close(self.output)
        _active = None


def data(value, *, indent=None):
    if _active is None:
        print(json.dumps(value, indent=indent))
    else:
        _active.event("data", value=value)


def error(code, message, *, prefix="raft"):
    if _active is None:
        print(f"{prefix}: {message}", file=sys.stderr, flush=True)
    else:
        _active.event("error", code=code, message=str(message))


def candidates(parser, words):
    """Walk argparse's command tree without loading configuration or running commands."""
    current = parser
    waiting = None
    for word in words[:-1]:
        if word == "--":
            return []
        if waiting is not None:
            waiting = None
            continue
        options = current._option_string_actions
        option, separator, _ = word.partition("=")
        if option in options:
            action = options[option]
            if action.nargs != 0 and not separator:
                waiting = action
            continue
        child = next(
            (a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None
        )
        if child and word in child.choices:
            current = child.choices[word]
    prefix = words[-1] if words else ""
    if waiting is not None:
        values = [str(value) for value in (waiting.choices or [])]
    else:
        values = list(current._option_string_actions)
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                values += [name for name in action.choices if not name.startswith("_")]
            elif not action.option_strings and action.choices:
                values += [str(value) for value in action.choices]
    return sorted({value for value in values if value.startswith(prefix)})


def completions(shell):
    # These wrappers contain no command catalog. Each completion reads the current
    # installed parser, including commands added after the script was generated.
    if shell == "bash":
        return """_raft_complete() {
  local IFS=$'\\n'
  COMPREPLY=( $(raft _complete -- "${COMP_WORDS[@]:1:COMP_CWORD}") )
}
complete -F _raft_complete raft
"""
    if shell == "zsh":
        return """#compdef raft
_raft_complete() {
  local -a matches
  matches=("${(@f)$(raft _complete -- "${words[@]:1:$((CURRENT-1))}")}")
  compadd -- "${matches[@]}"
}
compdef _raft_complete raft
"""
    return """function __raft_complete
  set -l tokens (commandline -opc)
  set -e tokens[1]
  raft _complete -- $tokens (commandline -ct)
end
complete -c raft -f -a '(__raft_complete)'
"""
