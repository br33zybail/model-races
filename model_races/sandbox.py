"""Run model-written Python in a separate, resource-limited process.

Every place in this package that executes model output goes through here, so
there is one set of rules to audit:

* a fresh interpreter started with `-I` (no user site-packages, no PYTHON*
  variables, script directory not on sys.path);
* a minimal environment: PATH, HOME and TMPDIR pointing at the run's own
  directory, LANG. Nothing else from the caller's environment is passed on, so
  API keys and tokens in your shell never reach model code;
* the working directory is a fresh temporary directory;
* rlimits set in the child before exec: address space, CPU seconds, file size,
  no core dumps and, for untrusted code, no new processes;
* a wall-clock timeout that kills the whole process group;
* stdout and stderr go to files (bounded by the file-size limit), not pipes,
  so a flood of output cannot exhaust the parent's memory;
* on Linux the calling process marks itself non-dumpable before the first
  run, so the child cannot read the caller's secrets from /proc/<ppid>/environ
  (same-user processes can otherwise read each other's environment there).

What this is not: a container. The child runs as your user and can read any
file your user can read, the /proc environment of your other processes, and
can open network connections. RLIMIT_NPROC does not apply to root. Run races
on a machine or container with no secrets.
"""
import ctypes
import json
import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass

MEM_BYTES = 2 << 30        # address space
CPU_SECONDS = 20
FILE_BYTES = 16 << 20      # largest file the child may write, stdout included
OUTPUT_TAIL = 64 << 10     # bytes of stdout/stderr read back


@dataclass
class Result:
    returncode: int | None   # None when the run timed out
    stdout: str
    stderr: str
    timed_out: bool


_PR_SET_DUMPABLE = 4
_hardened = False


def harden_caller():
    """Make this process non-dumpable, which makes /proc/<pid>/environ and
    /proc/<pid>/mem unreadable by other processes of the same user. Fails
    closed on Linux: no sandboxed run without it."""
    global _hardened
    if _hardened or not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed")
    _hardened = True


def _limits(mem_bytes, cpu_seconds, file_bytes, no_fork):
    def apply():
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if no_fork:
            # Counts every process of this user, so 0 means fork always fails.
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    return apply


def _tail(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - OUTPUT_TAIL))
        return f.read().decode("utf-8", "replace")


def run(args, *, cwd, timeout, untrusted=True, mem_bytes=MEM_BYTES,
        cpu_seconds=CPU_SECONDS, file_bytes=FILE_BYTES):
    """Run [python -I, *args] in `cwd` with a clean environment and rlimits.

    untrusted=False keeps the clean environment, memory, CPU and file limits but
    allows the child to start processes. Use it only for a trusted grader that
    must itself start a sandboxed runner.
    """
    harden_caller()
    env = {"PATH": "/usr/bin:/bin", "HOME": cwd, "TMPDIR": cwd, "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory() as io_dir:
        out_path = os.path.join(io_dir, "stdout")
        err_path = os.path.join(io_dir, "stderr")
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            p = subprocess.Popen(
                [sys.executable, "-I", *args], cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True,
                start_new_session=True,
                preexec_fn=_limits(mem_bytes, cpu_seconds, file_bytes, untrusted))
            try:
                rc, timed_out = p.wait(timeout=timeout), False
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
                rc, timed_out = None, True
        return Result(rc, _tail(out_path), _tail(err_path), timed_out)


def run_code(files, entry, *, timeout, **limits):
    """Write {name: text} into a fresh temporary directory and run `entry` there.

    File names must be plain names: no directories, no '..'."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            if os.path.basename(name) != name or name in ("", ".", ".."):
                raise ValueError(f"unsafe file name: {name!r}")
            with open(os.path.join(td, name), "w") as f:
                f.write(text)
        return run([entry], cwd=td, timeout=timeout, **limits)


def run_grader(grade_py, workspace, *, timeout=120):
    """Run a trusted fixture grader on `workspace` with a clean environment.
    -> (score | None, grade_error). The grader prints `SCORE <0..1>` last; any
    crash, timeout or other output is None, never a number."""
    with tempfile.TemporaryDirectory() as td:
        r = run([grade_py, os.path.abspath(workspace)], cwd=td, timeout=timeout,
                untrusted=False)
    if r.timed_out:
        return None, "grader timeout"
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    m = re.fullmatch(r"SCORE ([01](?:\.\d+)?)", lines[-1]) if lines else None
    if r.returncode != 0 or not m:
        last = lines[-1][:80] if lines else "<no output>"
        return None, f"grade-error rc={r.returncode} last={last!r}"
    return float(m.group(1)), ""


def last_json(stdout):
    """The last non-empty stdout line parsed as a JSON object, or None."""
    lines = [l for l in stdout.splitlines() if l.strip()]
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# Runs candidate code and a test module in one namespace. The candidate's own
# prints are swallowed so they cannot land after the result line. A submission
# written to attack the grader can still forge a result (it shares the process
# with the tests); this stops accidents, not a hostile model.
CANDIDATE_RUNNER = r'''
import contextlib, io, json, os, sys
def emit(obj):
    os.write(1, (json.dumps(obj) + "\n").encode())
ns = {}
try:
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(open("cand.py").read(), "cand.py", "exec"), ns)
except BaseException as e:
    emit({"passed": 0, "total": None, "err": f"exec: {type(e).__name__}: {e}"}); sys.exit()
try:
    exec(compile(open("tests.py").read(), "tests.py", "exec"), ns)
    with contextlib.redirect_stdout(io.StringIO()):
        res = ns["run_tests"](ns)
except BaseException as e:
    emit({"passed": 0, "total": None, "err": f"harness: {type(e).__name__}: {e}"}); sys.exit()
emit(res)
'''


def run_candidate(code, tests, *, timeout=60):
    """Execute candidate `code`, then `tests` (which defines run_tests(ns) and
    returns a JSON-able dict). -> (result dict | None, failure reason)."""
    r = run_code({"cand.py": code, "tests.py": tests, "run.py": CANDIDATE_RUNNER},
                 "run.py", timeout=timeout)
    if r.timed_out:
        return None, f"timeout ({timeout}s)"
    res = last_json(r.stdout)
    if res is None:
        return None, f"no result: {(r.stderr or r.stdout)[-70:]}"
    return res, ""
