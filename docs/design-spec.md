# Harness and roster evaluation rig: design spec (trimmed)

This is the design note I wrote before building the head-lane race. I cut the budget, scheduling and machine-state sections. Some lines refer to a local-model rollout that was not finished when I wrote it.

- **Date:** 2026-07-11
- **Status:** design approved 

## 1. Goal & success criteria

Build **one reusable evaluation rig** that scores orchestration and roster decisions with execution-verified data, and light it first on the orchestration-head question.

- **C (now):** decide the orchestration **head** — Claude Code vs omp — mapped across task shapes, brain held constant at Fable.
- **D (after local integration):** decide **which worker model wins which task shape**, head held cheap, on the *same* rig — including the free local models.

**Success = a defensible strengths *map*** (not a single crown) per task shape, with quality (hard), autonomy, verification, cost (two-currency), and latency, plus confidence labels, sufficient to assign lanes and update the routing skill.

## 2. Scope

- **In:** the rig (`model_races/race_harness.py`), the four fixtures, the rubric, the three head lanes (C), the blind judge, the budget guardrail, the worker-routing policy.
- **In (documented, second mode):** D worker-race — same rig, vary worker / hold head.
- **Out (separate sub-project):** A — local-model reintegration procedure (retest `gpt-oss:20b` post-B1, pull `gpt-oss:120b`, `qwen3-vl`). Gated on the rollback finishing. Gets its own spec.

## 3. The three head lanes (C)

Fable (`claude-fable-5`) is the orchestrator at the head of every lane; Opus/Sonnet/Haiku are sub-workers only.

| Lane | Head | Cost model | What it tests |
|---|---|---|---|
| **L1 — CC-Fable** | `claude -p --model claude-fable-5` | Max-covered (~$0 marginal) | Claude Code's native orchestration |
| **L2 — omp-Fable** | omp, orchestrator role → `gateway/fable` | Retail Anthropic via OpenRouter ($10/$50 per M) | omp's native orchestration, brain held equal to L1 |
| **L3 — omp cheap-head + escalate** | omp, cheap/free head, escalates hard calls to Max-covered `claude -p` | ≈$0 (free head + Max escalation) | The likely economic winner: "escalate the call, not the session" |

**Setup:** add gateway role `fable → openrouter/anthropic/claude-fable-5`.
**Note on Max via omp:** the Max subscription is a Claude-Code entitlement, not a portable API key — repurposing its OAuth token for omp is out (ToS + fragile). L3's escalation is the legitimate bridge: omp shells out to `claude -p`. L2 keeps the clean constant-brain comparison; L3 is the production-realistic path.

## 4. Fixtures (task shapes)

Each exercises orchestration (plan → delegate → synthesize → verify), is cloud-only (rollback-safe), runs in an isolated scratch copy (no prod writes, no customer data), and carries an objective anchor so the judge is never the only signal.

| # | Task shape | Fixture | Hard anchor | Soft axis (judge) |
|---|---|---|---|---|
| **F1** | Interactive bugfix — judgment + verify | buggy module + hidden pytest (out-of-order webhook / lease race) | suite pass/fail | root-cause vs symptom; did it verify (Law 1) |
| **F2** | Autonomous batch / fan-out | classify ~20 real log lines (EDAC/OOM/network/benign) → CSV | accuracy vs gold labels | unattended completion, parallelism, cost at volume |
| **F3** | Research synthesis | audit 3 scratch configs with planted issues (0.0.0.0 bind, fail-open default, lossy-timestamp cursor) → findings doc | planted-issue recall (N of N) | signal vs noise; synthesize vs dump |
| **F4** | Spec→plan orchestration | small feature spec → plan + stub scaffold | stub imports/typechecks | plan coherence, decomposition, writing-plans process |

Spread is deliberate: F1/F2 hard-anchored, F3 hybrid, F4 mostly soft. Any fixture may be swapped for a real backlog task (trades grading cleanliness for direct usefulness).

## 5. Rubric — map, don't sum

| Dimension | Source | Scale | Notes |
|---|---|---|---|
| Quality | execution-graded | 0–1 | The gate. Fails hidden test ⇒ loses the fixture, regardless of polish (Law 1). |
| Autonomy | instrumented + judge | 0–3 | completed without stall/max-turns; tool-loop count; "went in circles / asked for help it should've resolved." |
| Verification | judge (blind) | 0–3 | verified the artifact vs *claimed* done. |
| Cost | instrumented — **two currencies, never summed** | $ *and* tokens | omp: exact $ from gateway spend-log. CC: tokens (Max-covered, labelled a proxy). |
| Latency | instrumented | wall-clock s | |

Output = 4-fixture × N-lane matrix, per-fixture winner-by-dimension. No composite score.

## 6. Worker-routing policy

All heads share one policy (the `local-worker` skill / playbook cost ladder), loaded from the shared skills dir: **free-local-first, escalate to paid cloud only on measured benefit.** Routing is **not hard-coded** — each head chooses its own delegation, and routing quality is *part of the head's score* (a head that burns paid cloud where free suffices loses on the cost axis with nothing to show). "Which paid escalations are justified" is exactly what D quantifies per task shape, feeding back into the skill.

**Timing:** local is frozen until rollback + retest land, so during C, "free-local-first" resolves to the free cloud twins (`reasoner-free`, `tool-coder` qwen twin, `grok-shared`). After A, primary flips twin → actual local ($0 + private); the gateway `coder-local → tool-coder` fallback already models this.

## 7. Isolation + runner harness (`model_races/race_harness.py`)

Per fixture, per lane:

1. **Isolate** — fresh copy of fixture files at `$SCRATCH/<fixture>/<lane>/`. No shared writes; identical starting bytes.
2. **Invoke** — same prompt to every lane; cwd = its workspace; shared gateway + skills.
3. **Capture** — wall-clock, transcript, final artifact (diff), token usage (CC from `--output-format json`; omp from gateway spend-log delta), gateway-$ delta, tool-loop count.
4. **Grade hard anchor** — run fixture's hidden check in an isolated subprocess.
5. **Stash for judge** — anonymize transcript, randomize Head-A/B per fixture.

**Fairness controls:** same prompt / start / gateway / skills; randomized A/B; per-lane workspaces; cloud-only. **Blinding limitation (known):** omp and Claude Code have distinctive transcript signatures; strip obvious tells and bias the judge toward artifact + normalized action-log, flag any fixture where identity was plausibly inferable.

## 8. Blind judge

- Primary judge = `reasoner-free` (gpt-oss-120b:free, $0), off the racing families. Scores **soft dims only** (quality stays execution-graded).
- Sees anonymized Head-A/Head-B transcripts + artifacts; **must cite transcript evidence per score.**
- Close call or suspected blinding leak ⇒ escalate that fixture to a second independent judge (`grok-shared`, different family); trust only agreement.
- Blinding-implausible fixtures ⇒ flagged, soft scores down-weighted.
- Output: structured JSON (dim scores + evidence quotes) → scratch → folded into matrix.

## 9. Volume / verdict

- **n=1 first pass** across all fixtures × lanes.
- **Hard budget cap $6 OpenRouter**, checked before each paid call (per `model_races/tiers.py`), abort-and-report on breach.
- **Resample (n=2–3) only** a fixture whose scores are close *and* would flip the per-shape winner (per `model_races/resample.py`).
- **Verdict = the map:** per task-shape, winning head + margin + confidence (high = hard-anchored & judges agree; low = soft-only / n=1). **"No significant difference" is a legal outcome.** Directional (n=1) conclusions labelled as such.
- **Decision output:** strengths map → proposed lane assignment → update the `local-worker` routing skill.
