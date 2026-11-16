"""The sandbox helper must contain model-written code: no inherited secrets
(directly or through /proc), no runaway time, memory, processes or output."""
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from model_races import config, hard_task, sandbox, tiers
from model_races import race_harness as rh

SECRET = "race-test-secret-7f3a91"


def _snippet(code, timeout=10, **limits):
    return sandbox.run_code({"s.py": code}, "s.py", timeout=timeout, **limits)


def test_env_secret_not_visible(monkeypatch):
    monkeypatch.setenv("RACE_SECRET_PROBE", SECRET)
    r = _snippet("import os, json\n"
                 "print(json.dumps({'v': os.environ.get('RACE_SECRET_PROBE'),"
                 " 'keys': sorted(os.environ)}))")
    out = sandbox.last_json(r.stdout)
    assert out["v"] is None
    assert set(out["keys"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "LC_CTYPE"}


def test_parent_environ_not_readable_through_proc():
    # The secret must sit in the caller's *initial* environment for
    # /proc/<pid>/environ to show it, so the caller is a fresh interpreter.
    probe = ("import os, json\n"
             "found = []\n"
             "for pid in (os.getppid(),):\n"
             "    try: found.append(%r in open(f'/proc/{pid}/environ').read())\n"
             "    except OSError: found.append(False)\n"
             "print(json.dumps({'own': os.environ.get('RACE_SECRET_PROBE'),"
             " 'proc': any(found)}))") % SECRET
    driver = ("import sys; sys.path.insert(0, %r)\n"
              "from model_races.sandbox import run_code\n"
              "print(run_code({'s.py': %r}, 's.py', timeout=10).stdout)"
              ) % (str(config.ROOT), probe)
    env = {"PATH": "/usr/bin:/bin", "RACE_SECRET_PROBE": SECRET}
    p = subprocess.run([sys.executable, "-I", "-c", driver], env=env,
                       capture_output=True, text=True, timeout=30)
    out = sandbox.last_json(p.stdout)
    assert out == {"own": None, "proc": False}, p.stderr


def test_wall_clock_timeout_is_contained():
    t0 = time.time()
    r = _snippet("import time\ntime.sleep(60)", timeout=2)
    assert r.timed_out and r.returncode is None
    assert time.time() - t0 < 10


def test_cpu_limit_kills_busy_loop():
    r = _snippet("while True: pass", timeout=30, cpu_seconds=1)
    assert not r.timed_out and r.returncode != 0


def test_memory_limit():
    r = _snippet("x = bytearray(4 << 30)\nprint('allocated')", mem_bytes=512 << 20)
    assert "allocated" not in r.stdout and r.returncode != 0


def test_cannot_start_processes():
    r = _snippet("import os, json\n"
                 "try:\n"
                 "    pid = os.fork()\n"
                 "    if pid == 0: os._exit(0)\n"
                 "    print(json.dumps({'forked': True}))\n"
                 "except OSError:\n"
                 "    print(json.dumps({'forked': False}))")
    if os.geteuid() == 0:
        pytest.skip("RLIMIT_NPROC does not bind root")
    assert sandbox.last_json(r.stdout) == {"forked": False}


def test_output_flood_is_bounded():
    r = _snippet("import os\nwhile True: os.write(1, b'x' * 65536)",
                 timeout=20, file_bytes=1 << 20)
    assert r.returncode != 0
    assert len(r.stdout) <= sandbox.OUTPUT_TAIL


def test_cwd_is_private_temp_dir(tmp_path):
    r = _snippet("import os; print(os.getcwd())")
    cwd = r.stdout.strip()
    assert cwd and not os.path.exists(cwd)          # removed after the run
    assert not cwd.startswith(str(config.ROOT))


def test_run_code_rejects_path_names():
    for bad in ("../x.py", "/tmp/x.py", "a/b.py", ".."):
        with pytest.raises(ValueError):
            sandbox.run_code({bad: "pass"}, "s.py", timeout=5)


# ---- callers go through the sandbox ---------------------------------------

GOOD_OFX = r'''
import re
def parse_ofx(text):
    out = []
    for block in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.S):
        f = dict(re.findall(r"<(\w+)>([^<\r\n]*)", block))
        d = f["DTPOSTED"][:8]
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "amount": float(f["TRNAMT"]),
                    "type": f["TRNTYPE"], "fitid": f["FITID"], "name": f["NAME"].strip(),
                    "memo": f.get("MEMO")})
    return out
'''


def test_hard_task_grades_in_subprocess(monkeypatch):
    monkeypatch.setenv("RACE_SECRET_PROBE", SECRET)
    assert hard_task.grade(GOOD_OFX) == (10, "PERFECT")
    leak = ("import os\n"
            "def parse_ofx(text):\n"
            "    return [{'name': os.environ.get('RACE_SECRET_PROBE')}]\n")
    score, detail = hard_task.grade(leak)
    assert score == 0 and SECRET not in detail


def test_hard_task_exec_error_is_zero():
    score, detail = hard_task.grade("def parse_ofx(:")
    assert score == 0 and detail.startswith("exec: SyntaxError")


def test_tiers_grade_uses_sandbox():
    passed, total, detail = tiers.grade(GOOD_OFX, tiers.TIERS[0]["tests"])
    assert total == 11 and passed >= 10
    passed, total, detail = tiers.grade("def parse_ofx(:", tiers.TIERS[0]["tests"])
    assert passed == 0 and total is None and detail.startswith("exec:")


# ---- head lanes: clean env, no silent fallback ----------------------------

def test_cc_lane_refuses_without_explicit_auth(monkeypatch, tmp_path):
    for k in rh.CC_AUTH_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(rh.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(rh.subprocess, "run",
                        lambda *a, **k: pytest.fail("lane must not start"))
    wall, tokens, cost, txt = rh._run_cc("prompt", str(tmp_path))
    assert txt.startswith("LANE_ERROR")


def test_cc_lane_env_and_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("RACE_SECRET_PROBE", SECRET)
    monkeypatch.setattr(rh.shutil, "which", lambda name: "/opt/bin/" + name)
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        assert os.path.isdir(kw["env"]["HOME"])
        return subprocess.CompletedProcess(cmd, 1, json.dumps(
            {"is_error": True, "result": "Not logged in"}), "")

    monkeypatch.setattr(rh.subprocess, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    *_, txt = rh._run_cc("prompt", str(ws))
    env = seen["env"]
    assert SECRET not in env.values()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert env["HOME"] == str(ws) + ".home" and not os.path.exists(env["HOME"])
    assert seen["cwd"] == str(ws)
    tools = seen["cmd"][seen["cmd"].index("--tools") + 1]
    assert "Bash" not in tools and "--restricted" in seen["cmd"]
    assert "--allowedTools" not in seen["cmd"]
    assert txt.startswith("LANE_ERROR")             # is_error JSON is not a run


def test_omp_lane_env_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("RACE_SECRET_PROBE", SECRET)
    monkeypatch.setattr(rh.shutil, "which", lambda name: "/opt/bin/" + name)
    monkeypatch.setattr(rh, "gw_since", lambda t: 0.0)
    seen = {}

    def fake_run(cmd, **kw):
        cfg = os.path.join(kw["env"]["HOME"], ".omp", "agent", "models.yml")
        seen.update(cmd=cmd, env=kw["env"], mode=stat.S_IMODE(os.stat(cfg).st_mode),
                    cfg=open(cfg).read())
        return subprocess.CompletedProcess(cmd, 0, 'Model "some-role" not found', "")

    monkeypatch.setattr(rh.subprocess, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    *_, txt = rh._run_omp("prompt", str(ws), "some-role")
    assert txt.startswith("LANE_ERROR")             # rc 0, but the head never ran
    assert SECRET not in seen["env"].values()
    assert set(seen["env"]) == {"PATH", "HOME", "TMPDIR", "LANG", "TERM"}
    assert seen["mode"] == 0o600 and config.BASE_URL in seen["cfg"]
    assert not os.path.exists(seen["env"]["HOME"])
    tools = seen["cmd"][seen["cmd"].index("--tools") + 1]
    assert "bash" not in tools.split(",")
    assert "--no-rules" in seen["cmd"] and "--no-skills" in seen["cmd"]
