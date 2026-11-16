#!/usr/bin/env python3
"""Escalating, execution-verified race: the six gpt-5.6 variants at reasoning
effort `xhigh` (the real ceiling -- `ultra`/`max` are silently ignored) against
the incumbent coder-heavy (GLM-5.2) and a free floor.

Design notes
------------
* Tiers escalate until a tier separates the field. A tier that everyone aces
  tells you nothing (our old fixtures ceiling'd -- that's why this exists).
* Grading EXECUTES the model's code against hidden tests. Regex grading gave us
  false failures before; execution is the only signal that survives scrutiny.
* Model code runs through sandbox.run_candidate: isolated interpreter, cleared
  env, temp cwd, rlimits, wall-clock timeout. The static screen below is a
  scoring rule, not a security boundary (it is easy to evade). Model output is
  untrusted input; run this on a machine with nothing valuable on it.
* Hard budget cap, checked BEFORE each call using a worst-case estimate.

Tiers 3 to 5 are shaped like real defect classes: a worker lease race that
clobbers terminal job state, a fork-choice rule that must refuse to reorg below
a finalized depth, and billing webhooks that arrive out of order.
"""
import json
import os
import re
import time
import urllib.request

from . import config
from .sandbox import run_candidate

GATEWAY = config.CHAT_URL
EFFORT = os.environ.get("EFFORT", "xhigh")
BUDGET = float(os.environ.get("BUDGET", "6.00"))
MAXTOK = 16000            # reasoning models think past small caps and return empty

# $/Mtok (in, out) -- from the OpenRouter models API, not memory. Set in the
# roster file ([tiers.prices]).
PRICES = {k: tuple(v) for k, v in config.ROSTER["tiers"]["prices"].items()}
CONTENDERS = list(PRICES)

# --- untrusted-code screen -------------------------------------------------
BANNED = re.compile(
    r"\b(os\.system|subprocess|socket|shutil\.rmtree|urllib|requests|httpx|"
    r"ctypes|multiprocessing|pty|fcntl|__import__\s*\(\s*['\"]os|eval\s*\(|"
    r"exec\s*\(|open\s*\(\s*['\"]/(?!tmp))"
)


def call(model, prompt):
    body = {"model": model, "max_tokens": MAXTOK,
            "messages": [{"role": "user", "content": prompt}]}
    if not model.endswith(":free"):
        body["reasoning_effort"] = EFFORT
    req = urllib.request.Request(GATEWAY, json.dumps(body).encode(), config.headers())
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1200) as r:
        d = json.load(r)
    dt = time.time() - t0
    txt = d["choices"][0]["message"]["content"] or ""
    u = d.get("usage", {})
    pin, pout = PRICES[model]
    cost = (u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout) / 1e6
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    return txt, dt, cost, u.get("completion_tokens", 0), rt


def extract_code(txt):
    m = re.findall(r"```(?:python)?\s*\n(.*?)```", txt, re.DOTALL)
    return (m[0] if m else txt).strip()


def grade(code, tests):
    if not code.strip():
        return 0, None, "empty output (thought past the cap?)"
    hit = BANNED.search(code)
    if hit:
        return 0, None, f"REJECTED unsafe construct: {hit.group()[:30]}"
    r, why = run_candidate(code, tests, timeout=60)
    if r is None:
        return 0, None, why
    return r.get("passed", 0), r.get("total"), r.get("err") or ""


# ---------------------------------------------------------------------------
TIERS = []

TIERS.append(dict(
    name="T1 OFX SGML parser",
    prompt=(
        "Write a Python function parse_ofx(text) that parses OFX 1.x SGML bank statements "
        "(tags are NOT closed like XML). Return a list of dicts, one per STMTTRN, with keys: "
        "date (ISO YYYY-MM-DD from DTPOSTED, ignore time and timezone suffixes like [-5:EST]), "
        "amount (float from TRNAMT), type (TRNTYPE), fitid (FITID), name (NAME), "
        "memo (MEMO or None if absent). Must not crash on fields you don't extract. "
        "Output ONLY the Python code in a ```python fence."),
    tests=r'''
def _score(checks):
    """Run each check in isolation: one exception must not zero the tier."""
    passed, failed = 0, []
    for name, fn in checks:
        try:
            if fn(): passed += 1
            else: failed.append(name)
        except Exception as e:
            failed.append(f"{name}({type(e).__name__})")
    return {"passed": passed, "total": len(checks), "err": ", ".join(failed[:4])}

OFX = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD
<BANKTRANLIST><DTSTART>20260601<DTEND>20260630
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260603120000[-5:EST]<TRNAMT>-125.50<FITID>2026060301<NAME>VENMO PAYMENT<MEMO>ACH WITHDRAWAL</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260615<TRNAMT>2500.00<FITID>2026061501<NAME>PAYROLL ACME CORP</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260620080000<TRNAMT>-42.17<FITID>2026062001<NAME>AMAZON MKTP<MEMO>CARD 1234</STMTTRN>
<STMTTRN><TRNTYPE>CHECK<DTPOSTED>20260628<TRNAMT>-300.00<FITID>2026062801<CHECKNUM>1042<NAME>CHECK 1042</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>4032.33</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
def run_tests(ns):
    tx = ns["parse_ofx"](OFX)
    C = [
      ("count",      lambda: len(tx)==4),
      ("date_tz",    lambda: tx[0]["date"]=="2026-06-03"),
      ("date_bare",  lambda: tx[1]["date"]=="2026-06-15"),
      ("neg_amt",    lambda: abs(float(tx[0]["amount"])+125.50)<1e-9),
      ("pos_amt",    lambda: abs(float(tx[1]["amount"])-2500.00)<1e-9),
      ("amt_is_float", lambda: isinstance(tx[0]["amount"], float)),
      ("type",       lambda: tx[3]["type"]=="CHECK"),
      ("fitid",      lambda: tx[2]["fitid"]=="2026062001"),
      ("name_clean", lambda: tx[1]["name"]=="PAYROLL ACME CORP"),
      ("memo",       lambda: tx[0]["memo"]=="ACH WITHDRAWAL"),
      ("memo_none",  lambda: tx[1]["memo"] is None),
    ]
    return _score(C)
'''))

TIERS.append(dict(
    name="T2 exact-money reconciliation",
    prompt=(
        "Write a Python function reconcile(rows, opening, closing) for bank statements.\n"
        "`rows` is a list of dicts with keys 'date' and 'amount', where amount is a STRING in any of these "
        "real-world shapes: '1,234.56' (thousands separators), '(45.00)' (parentheses mean NEGATIVE), "
        "'\\u221212.00' (U+2212 unicode minus), '$3.00', '12.00 CR' (credit => POSITIVE), "
        "'12.00 DR' (debit => NEGATIVE), '  7.50  ' (whitespace), '' or None (treat as 0).\n"
        "`opening` and `closing` are strings in the same formats.\n"
        "Return a tuple (total, ok) where `total` is the exact sum of all row amounts as a "
        "decimal.Decimal quantized to 2 places, and `ok` is True iff opening + total == closing EXACTLY "
        "to the cent. Money must be exact: never use binary floats for the arithmetic. "
        "A malformed amount that matches none of the shapes must raise ValueError.\n"
        "Output ONLY the Python code in a ```python fence."),
    tests=r'''
def _score(checks):
    """Run each check in isolation: one exception must not zero the tier."""
    passed, failed = 0, []
    for name, fn in checks:
        try:
            if fn(): passed += 1
            else: failed.append(name)
        except Exception as e:
            failed.append(f"{name}({type(e).__name__})")
    return {"passed": passed, "total": len(checks), "err": ", ".join(failed[:4])}

from decimal import Decimal
def run_tests(ns):
    f = ns["reconcile"]
    def malformed():
        try: f([{"date":"x","amount":"twelve"}],"0.00","0.00"); return False
        except ValueError: return True
        except Exception: return False
    C = [
      ("float_trap",  lambda: f([{"date":"x","amount":"0.10"},{"date":"x","amount":"0.20"}],"0.00","0.30")==(Decimal("0.30"),True)),
      ("parens_neg",  lambda: f([{"date":"x","amount":"(45.00)"}],"100.00","55.00")[1] is True),
      ("uni_minus",   lambda: f([{"date":"x","amount":"\u221212.00"}],"12.00","0.00")[1] is True),
      ("thousands",   lambda: f([{"date":"x","amount":"$1,234.56"}],"0.00","1234.56")[1] is True),
      ("cr_dr",       lambda: f([{"date":"x","amount":"12.00 CR"},{"date":"x","amount":"5.00 DR"}],"0.00","7.00")[1] is True),
      ("empty_none",  lambda: f([{"date":"x","amount":""},{"date":"x","amount":None},{"date":"x","amount":"1.00"}],"0.00","1.00")[1] is True),
      ("mismatch",    lambda: f([{"date":"x","amount":"1.00"}],"0.00","2.00")[1] is False),
      ("decimal_type",lambda: isinstance(f([{"date":"x","amount":"1.00"}],"0.00","1.00")[0], Decimal)),
      ("quantized",   lambda: f([{"date":"x","amount":"1.00"}],"0.00","1.00")[0].as_tuple().exponent==-2),
      ("no_float_drift", lambda: f([{"date":"x","amount":"0.10"}]*3,"0.00","0.30")==(Decimal("0.30"),True)),
      ("malformed_raises", malformed),
    ]
    return _score(C)
'''))

TIERS.append(dict(
    name="T3 worker lease state machine",
    prompt=(
        "Implement a Python class LeaseManager for a job queue where slow workers must never "
        "corrupt finished work.\n\n"
        "  LeaseManager(ttl)                     # lease time-to-live, integer seconds\n"
        "  add(job_id)                           # enqueue a job\n"
        "  claim(worker, now) -> job_id | None   # claim oldest claimable job, take a lease\n"
        "  complete(job_id, worker, now) -> bool # mark done\n"
        "  fail(job_id, worker, now) -> bool     # mark failed\n"
        "  status(job_id) -> 'queued'|'running'|'done'|'failed'\n\n"
        "Rules, all of which are tested:\n"
        "1. A job in a terminal state ('done'/'failed') NEVER changes state again; complete/fail on it "
        "   return False (except rule 5).\n"
        "2. complete/fail succeed ONLY if `worker` currently holds a non-expired lease on that job. "
        "   A worker whose lease expired (now >= lease_expiry) must get False -- this is the stale-writer "
        "   case where a slow worker would otherwise clobber another worker's result.\n"
        "3. A job whose lease expired becomes claimable again by any worker; the new claim invalidates "
        "   the old holder's lease.\n"
        "4. claim() never returns a job that is 'running' with a live lease, nor a terminal job. "
        "   Returns None when nothing is claimable.\n"
        "5. complete() called twice by the SAME worker while still holding the lease is idempotent: "
        "   returns True both times, status stays 'done'.\n"
        "6. Lease expiry is exclusive at the boundary: at now == claim_time + ttl the lease IS expired.\n"
        "7. complete()/fail() on an unknown job_id return False; they never raise.\n"
        "Output ONLY the Python code in a ```python fence."),
    tests=r'''
def _score(checks):
    """Run each check in isolation: one exception must not zero the tier."""
    passed, failed = 0, []
    for name, fn in checks:
        try:
            if fn(): passed += 1
            else: failed.append(name)
        except Exception as e:
            failed.append(f"{name}({type(e).__name__})")
    return {"passed": passed, "total": len(checks), "err": ", ".join(failed[:4])}

def run_tests(ns):
    LM = ns["LeaseManager"]
    def basic():
        m=LM(30); m.add("a")
        return m.claim("w1",0)=="a" and m.status("a")=="running"
    def complete_ok():
        m=LM(30); m.add("a"); m.claim("w1",0)
        return m.complete("a","w1",1) is True and m.status("a")=="done"
    def terminal_immutable():
        m=LM(30); m.add("a"); m.claim("w1",0); m.complete("a","w1",1)
        return m.fail("a","w1",2) is False and m.status("a")=="done"
    def claim_skips_terminal():
        m=LM(30); m.add("a"); m.claim("w1",0); m.complete("a","w1",1)
        return m.claim("w2",3) is None
    def live_lease_not_reclaimable():
        m=LM(30); m.add("b"); m.claim("w1",0)
        return m.claim("w2",5) is None
    def expiry_boundary_exclusive():
        m=LM(30); m.add("b"); m.claim("w1",0)
        return m.claim("w2",30)=="b"
    def stale_writer_rejected():
        m=LM(30); m.add("b"); m.claim("w1",0); m.claim("w2",30)
        return m.complete("b","w1",31) is False
    def holder_wins():
        m=LM(30); m.add("b"); m.claim("w1",0); m.claim("w2",30)
        return m.complete("b","w2",31) is True and m.status("b")=="done"
    def idempotent():
        m=LM(30); m.add("c"); m.claim("w1",0)
        return m.complete("c","w1",1) is True and m.complete("c","w1",2) is True and m.status("c")=="done"
    def expired_cannot_complete():
        m=LM(10); m.add("d"); m.claim("w1",0)
        return m.complete("d","w1",10) is False and m.status("d")!="done"
    def real_bug_slow_worker():
        m=LM(10); m.add("e"); m.claim("A",0); m.claim("B",10)
        ok1 = m.fail("e","B",11) is True
        ok2 = m.complete("e","A",12) is False and m.status("e")=="failed"
        return ok1 and ok2
    def unknown_job():
        m=LM(30)
        return m.complete("nosuch","w",1) is False and m.fail("nosuch","w",1) is False
    C=[("basic",basic),("complete",complete_ok),("terminal_immutable",terminal_immutable),
       ("claim_skips_terminal",claim_skips_terminal),("live_lease",live_lease_not_reclaimable),
       ("expiry_boundary",expiry_boundary_exclusive),("stale_writer",stale_writer_rejected),
       ("holder_wins",holder_wins),("idempotent",idempotent),
       ("expired_no_complete",expired_cannot_complete),("SLOW_WORKER_CLOBBER",real_bug_slow_worker),
       ("unknown_job",unknown_job)]
    return _score(C)
'''))

TIERS.append(dict(
    name="T4 fork choice with finalized depth",
    prompt=(
        "Implement a Python class ChainSelector modelling a proof-of-work node's fork choice, "
        "with the safety rule that a node must never reorg below its finalized depth.\n\n"
        "  ChainSelector(genesis_hash, finalized_depth)\n"
        "  add_block(block_hash, parent_hash, work) -> bool   # returns True iff the tip changed\n"
        "  tip() -> hash\n"
        "  height(block_hash) -> int          # genesis is height 0\n"
        "  is_canonical(block_hash) -> bool   # on the path from genesis to tip\n\n"
        "Rules, all tested:\n"
        "1. The canonical chain is the one with the greatest CUMULATIVE work (sum of `work` from genesis). "
        "   Height does not decide; work does.\n"
        "2. Ties in cumulative work are broken by FIRST SEEN: the incumbent tip keeps the title.\n"
        "3. A block whose parent is unknown is ignored: add_block returns False and it is not stored "
        "   (no orphan pool).\n"
        "4. FINALITY: let `finalized_height = height(tip) - finalized_depth` (never below 0). A candidate "
        "   chain may only become canonical if it still contains the CURRENT canonical block at "
        "   finalized_height. Otherwise the reorg is refused, add_block returns False, and the tip is "
        "   unchanged -- even if that candidate has more cumulative work. (This is what strands a node "
        "   on a minority fork, and it is intentional.)\n"
        "5. Re-adding a known block is a no-op returning False.\n"
        "Output ONLY the Python code in a ```python fence."),
    tests=r'''
def _score(checks):
    """Run each check in isolation: one exception must not zero the tier."""
    passed, failed = 0, []
    for name, fn in checks:
        try:
            if fn(): passed += 1
            else: failed.append(name)
        except Exception as e:
            failed.append(f"{name}({type(e).__name__})")
    return {"passed": passed, "total": len(checks), "err": ", ".join(failed[:4])}

def run_tests(ns):
    CS = ns["ChainSelector"]
    def linear():
        c=CS("g",100); return c.add_block("a","g",1) is True and c.tip()=="a" and c.height("a")==1
    def unknown_parent():
        c=CS("g",100); c.add_block("a","g",1)
        return c.add_block("x","nope",99) is False and c.tip()=="a"
    def duplicate():
        c=CS("g",100); c.add_block("a","g",1); return c.add_block("a","g",1) is False
    def work_beats_height():
        c=CS("g",100); c.add_block("a","g",1); c.add_block("b","a",1)
        return c.tip()=="b" and c.add_block("H","g",5) is True and c.tip()=="H"
    def canonical_flips():
        c=CS("g",100); c.add_block("a","g",1); c.add_block("b","a",1); c.add_block("H","g",5)
        return c.is_canonical("H") and not c.is_canonical("b")
    def tie_first_seen():
        c=CS("g",100); c.add_block("a","g",3)
        return c.add_block("z","g",3) is False and c.tip()=="a"
    def finality_refuses_deep_reorg():
        c=CS("g",1); c.add_block("a","g",1); c.add_block("b","a",1)
        return c.add_block("f1","g",50) is False and c.tip()=="b"
    def shallow_reorg_allowed():
        c=CS("g",1); c.add_block("a","g",1); c.add_block("b","a",1)
        return c.add_block("b2","a",50) is True and c.tip()=="b2"
    def clamp_at_zero():
        c=CS("g",10); c.add_block("a","g",1)
        return c.add_block("a2","g",5) is True and c.tip()=="a2"
    def cumulative_work():
        c=CS("g",100); c.add_block("a","g",1); c.add_block("b","a",1); c.add_block("c","b",1)
        return c.add_block("H","g",4) is True and c.tip()=="H"
    def stranded_branch():
        c=CS("g",100); c.add_block("a","g",1); c.add_block("b","a",1); c.add_block("c","b",1); c.add_block("H","g",4)
        return all(not c.is_canonical(h) for h in ("a","b","c"))
    C=[("linear",linear),("unknown_parent",unknown_parent),("duplicate",duplicate),
       ("work_beats_height",work_beats_height),("canonical_flips",canonical_flips),
       ("tie_first_seen",tie_first_seen),("FINALITY_REFUSES_REORG",finality_refuses_deep_reorg),
       ("shallow_reorg_ok",shallow_reorg_allowed),("clamp_zero",clamp_at_zero),
       ("cumulative_work",cumulative_work),("stranded_branch",stranded_branch)]
    return _score(C)
'''))


TIERS.append(dict(
    name="T5 out-of-order entitlement webhooks",
    prompt=(
        "Implement a Python class Entitlements that decides a customer's paid plan from Stripe-like "
        "webhook events. Events arrive DUPLICATED and OUT OF ORDER, and getting this wrong means "
        "billing a customer who lost access, or giving access to one who cancelled.\n\n"
        "  Entitlements()\n"
        "  process(event) -> bool     # True iff entitlement state changed\n"
        "  plan_of(customer) -> str   # 'free' if unknown\n\n"
        "An event is a dict: id (unique str), type in {'created','updated','deleted'}, created (int epoch), "
        "customer (str), subscription (str), plan in {'starter','business'}, status in "
        "{'active','past_due','canceled'}.\n\n"
        "Rules, all tested:\n"
        "1. IDEMPOTENT REPLAY: an event id already processed is ignored -> False, no state change.\n"
        "2. PER-SUBSCRIPTION ORDERING: apply an event only if its `created` is strictly greater than the "
        "   newest `created` already applied for that SUBSCRIPTION. On equal `created`, the "
        "   lexicographically greater event `id` wins. Stale events return False and change nothing.\n"
        "3. 'deleted' sets the customer to 'free' ONLY if that subscription is the customer's current "
        "   entitling subscription. A delayed 'deleted' for an OLD subscription must NOT downgrade a "
        "   customer who has since resubscribed on a NEW subscription.\n"
        "4. status 'past_due' RETAINS the current plan (deliberate dunning grace), and is not a downgrade.\n"
        "5. status 'canceled' on a 'created'/'updated' event sets the customer to 'free' (same "
        "   current-subscription rule as 3).\n"
        "6. plan_of() on an unknown customer returns 'free'.\n"
        "Output ONLY the Python code in a ```python fence."),
    tests=r'''
def _score(checks):
    passed, failed = 0, []
    for name, fn in checks:
        try:
            if fn(): passed += 1
            else: failed.append(name)
        except Exception as e:
            failed.append(f"{name}({type(e).__name__})")
    return {"passed": passed, "total": len(checks), "err": ", ".join(failed[:4])}

def ev(i, ty, cr, cus, sub, plan="starter", st="active"):
    return {"id":i,"type":ty,"created":cr,"customer":cus,"subscription":sub,"plan":plan,"status":st}

def run_tests(ns):
    E = ns["Entitlements"]
    def unknown_free():
        return E().plan_of("nobody")=="free"
    def basic_create():
        e=E(); ch=e.process(ev("e1","created",100,"c1","s1","business"))
        return ch is True and e.plan_of("c1")=="business"
    def replay_idempotent():
        e=E(); e.process(ev("e1","created",100,"c1","s1","business"))
        return e.process(ev("e1","created",100,"c1","s1","business")) is False and e.plan_of("c1")=="business"
    def out_of_order_stale():
        e=E(); e.process(ev("e2","updated",200,"c1","s1","business"))
        ch=e.process(ev("e1","created",100,"c1","s1","starter"))
        return ch is False and e.plan_of("c1")=="business"
    def tie_break_by_id():
        e=E(); e.process(ev("aaa","updated",100,"c1","s1","starter"))
        ch=e.process(ev("zzz","updated",100,"c1","s1","business"))
        return ch is True and e.plan_of("c1")=="business"
    def tie_break_loser():
        e=E(); e.process(ev("zzz","updated",100,"c1","s1","business"))
        ch=e.process(ev("aaa","updated",100,"c1","s1","starter"))
        return ch is False and e.plan_of("c1")=="business"
    def past_due_grace():
        e=E(); e.process(ev("e1","created",100,"c1","s1","business"))
        e.process(ev("e2","updated",200,"c1","s1","business",st="past_due"))
        return e.plan_of("c1")=="business"
    def canceled_downgrades():
        e=E(); e.process(ev("e1","created",100,"c1","s1","business"))
        ch=e.process(ev("e2","updated",200,"c1","s1","business",st="canceled"))
        return ch is True and e.plan_of("c1")=="free"
    def delete_downgrades():
        e=E(); e.process(ev("e1","created",100,"c1","s1","business"))
        return e.process(ev("e2","deleted",200,"c1","s1","business")) is True and e.plan_of("c1")=="free"
    def DELAYED_DELETE_OLD_SUB():
        # the real bug: cancel s1, resubscribe s2, then s1's delayed 'deleted' lands
        e=E()
        e.process(ev("e1","created",100,"c1","s1","starter"))
        e.process(ev("e2","deleted",200,"c1","s1","starter"))
        e.process(ev("e3","created",300,"c1","s2","business"))
        ch=e.process(ev("e4","deleted",250,"c1","s1","starter"))   # delayed, for OLD sub
        return e.plan_of("c1")=="business"
    def delete_old_sub_no_change():
        e=E()
        e.process(ev("e1","created",100,"c1","s1","starter"))
        e.process(ev("e3","created",300,"c1","s2","business"))
        ch=e.process(ev("e4","deleted",400,"c1","s1","starter"))   # old sub deleted later
        return e.plan_of("c1")=="business"
    def per_sub_ordering_independent():
        e=E()
        e.process(ev("a1","created",500,"c1","s1","starter"))
        ch=e.process(ev("b1","created",100,"c1","s2","business"))  # older but DIFFERENT sub
        return ch is True
    C=[("unknown_free",unknown_free),("basic_create",basic_create),
       ("replay_idempotent",replay_idempotent),("out_of_order_stale",out_of_order_stale),
       ("tie_break_by_id",tie_break_by_id),("tie_break_loser",tie_break_loser),
       ("past_due_grace",past_due_grace),("canceled_downgrades",canceled_downgrades),
       ("delete_downgrades",delete_downgrades),
       ("DELAYED_DELETE_OLD_SUB",DELAYED_DELETE_OLD_SUB),
       ("delete_old_sub_no_change",delete_old_sub_no_change),
       ("per_sub_ordering",per_sub_ordering_independent)]
    return _score(C)
'''))


def main():
    spent = 0.0
    field = list(CONTENDERS)
    results = {}
    print(f"effort={EFFORT}  budget=${BUDGET:.2f}  contenders={len(field)}\n")
    for tier in TIERS:
        if len(field) < 2:
            print(f"-- stopping: {len(field)} contender(s) left, separation achieved\n")
            break
        print(f"=== {tier['name']} ===")
        tier_scores = {}
        for model in field:
            pin, pout = PRICES[model]
            worst = (2000 * pin + MAXTOK * pout) / 1e6
            if spent + worst > BUDGET:
                print(f"  {model.split('/')[-1]:18} SKIPPED (budget: ${spent:.2f} + est ${worst:.2f} > ${BUDGET:.2f})")
                continue
            try:
                txt, dt, cost, tok, rt = call(model, tier["prompt"])
                spent += cost
                passed, total, detail = grade(extract_code(txt), tier["tests"])
            except Exception as e:
                print(f"  {model.split('/')[-1]:18} ERROR {str(e)[:60]}")
                tier_scores[model] = -1
                continue
            tier_scores[model] = passed / total if total else 0.0
            results.setdefault(model, {})[tier["name"]] = (passed, total, cost, dt)
            flag = "PERFECT" if total and passed == total else detail[:44]
            print(f"  {model.split('/')[-1]:18} {passed}/{total}  {dt:6.1f}s  "
                  f"{tok:6d}tok r={rt:<6} ${cost:.3f}  {flag}")
        print(f"  -- tier spend so far: ${spent:.2f}\n")
        survivors = [m for m, s in tier_scores.items() if s >= 1.0]
        if not survivors:
            best = max(tier_scores.values() or [0])
            survivors = [m for m, s in tier_scores.items() if s == best and s > 0]
            print(f"  no perfect score; carrying top scorers ({best:.0%}) forward\n")
        field = survivors

    print("=" * 78)
    print(f"TOTAL SPEND: ${spent:.2f} of ${BUDGET:.2f}")
    print("Survivors:", ", ".join(m.split("/")[-1] for m in field) or "none")
    print("\nMatrix (passed/total, cost, latency):")
    for m, t in results.items():
        row = "  ".join(f"{k.split()[0]}:{v[0]}/{v[1]}" for k, v in t.items())
        tot = sum(v[2] for v in t.values())
        print(f"  {m.split('/')[-1]:18} {row}   ${tot:.3f}")


if __name__ == "__main__":
    main()
