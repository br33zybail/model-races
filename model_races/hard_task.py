"""Usage: python3 -m model_races.hard_task <role> [role ...]
One hard task (an OFX parser), graded by running the returned code."""
import json, os, re, sys, time, urllib.request

from . import config
from .sandbox import run_candidate

# Set RACE_BASE_URL to race a raw OpenAI-compatible server (e.g. a local
# llama-server on :8080) without adding a role to your gateway.
ENDPOINT = config.CHAT_URL

OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD
<BANKTRANLIST><DTSTART>20260601<DTEND>20260630
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260603120000[-5:EST]<TRNAMT>-125.50<FITID>2026060301<NAME>VENMO PAYMENT<MEMO>ACH WITHDRAWAL</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260615<TRNAMT>2500.00<FITID>2026061501<NAME>PAYROLL ACME CORP</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260620080000<TRNAMT>-42.17<FITID>2026062001<NAME>AMAZON MKTP<MEMO>CARD 1234</STMTTRN>
<STMTTRN><TRNTYPE>CHECK<DTPOSTED>20260628<TRNAMT>-300.00<FITID>2026062801<CHECKNUM>1042<NAME>CHECK 1042</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>4032.33<DTASOF>20260630</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""

PROMPT = ("Write a Python function parse_ofx(text) that parses OFX 1.x SGML bank statements "
"(tags are NOT closed like XML). Return a list of dicts, one per STMTTRN, with keys: "
"date (ISO YYYY-MM-DD from DTPOSTED, ignore time and timezone suffixes like [-5:EST]), "
"amount (float from TRNAMT), type (TRNTYPE), fitid (FITID), name (NAME), "
"memo (MEMO or None if absent). Must not crash on fields you don't extract. "
"Output ONLY the Python code, no explanation, no markdown fences.")

def ask(role):
    # Thinking-heavy models burn the whole budget reasoning and emit no code at
    # all (Qwen3.8-27B, 2026-08-15: 8000 tok, zero output). Raise via MAXTOK,
    # and set NOTHINK=1 to run the model's non-thinking mode where supported.
    req_body = {"model": role, "max_tokens": int(os.environ.get("MAXTOK", 8000)),
        "messages":[{"role":"user","content":PROMPT}]}
    if os.environ.get("NOTHINK"):
        req_body["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(req_body).encode()
    req = urllib.request.Request(ENDPOINT, body, config.headers())
    t0=time.time()
    with urllib.request.urlopen(req, timeout=600) as r: d=json.load(r)
    code = d["choices"][0]["message"]["content"] or ""
    code = re.sub(r"^```(python)?|```$", "", code.strip(), flags=re.M)
    u=d.get("usage",{})
    return code, time.time()-t0, u.get("completion_tokens",0)

# The checks run in a sandboxed subprocess (sandbox.run_candidate), never in
# this process: the parser under test is model output.
TESTS = "OFX = " + repr(OFX) + r'''

def run_tests(ns):
    try: tx = ns["parse_ofx"](OFX)
    except Exception as e: return {"passed": 0, "total": 10, "err": f"runtime error: {e}"}
    # Checks are callables, not pre-evaluated booleans: a partially-correct
    # parser (3 transactions, or a None amount) used to raise inside this list
    # and take the whole grade down to ERROR. Found in the DeepSeek V4 Flash
    # race, 2026-08-11.
    checks = [
        ("4 transactions", lambda: len(tx)==4),
        ("date w/ tz+time", lambda: tx[0]["date"]=="2026-06-03"),
        ("date bare", lambda: tx[1]["date"]=="2026-06-15"),
        ("neg amount", lambda: abs(tx[0]["amount"]+125.50)<1e-9),
        ("pos amount", lambda: abs(tx[1]["amount"]-2500.00)<1e-9),
        ("type", lambda: tx[3]["type"]=="CHECK"),
        ("fitid", lambda: tx[2]["fitid"]=="2026062001"),
        ("name not bleeding", lambda: tx[1]["name"]=="PAYROLL ACME CORP"),
        ("memo present", lambda: tx[0]["memo"]=="ACH WITHDRAWAL"),
        ("memo absent -> None", lambda: tx[1]["memo"] is None),
    ]

    def run_check(name, fn):
        """Evaluate one check that may raise (IndexError, TypeError, KeyError).

        Returns (passed: bool, label: str). The label is what shows up in the
        FAILS list, so it carries why the check blew up. A raising check fails
        only itself; the exception type is tagged so a parser that CRASHES on
        real input stays distinguishable from one that merely got a value wrong
        (Jackson, 2026-08-15).
        """
        try:
            return bool(fn()), name
        except Exception as e:
            return False, f"{name} (raised {type(e).__name__})"

    results = [run_check(n, fn) for n, fn in checks]
    return {"passed": sum(1 for ok, _ in results if ok), "total": len(checks),
            "fails": [label for ok, label in results if not ok]}
'''


def grade(code):
    res, why = run_candidate(code, TESTS, timeout=60)
    if res is None:
        return 0, why
    if res.get("total") is None or "fails" not in res:
        return 0, res.get("err") or "no result"
    fails = res["fails"]
    return res.get("passed", 0), ("FAILS: "+", ".join(fails)) if fails else "PERFECT"


if __name__ == "__main__":
    for role in sys.argv[1:]:
        try:
            code, dt, tok = ask(role)
            score, detail = grade(code)
            print(f"{role:16s} {score}/10  {dt:5.1f}s  {tok:5d} tok  {detail}")
        except Exception as e:
            print(f"{role:16s} ERROR: {str(e)[:80]}")
