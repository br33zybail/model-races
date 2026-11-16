# model-races

A small harness I use to race language models against each other on my own tasks before I trust one with a job. Each task has a grader that runs the model's code or checks its file. No regex on prose decides a score.

## Why

Public leaderboards tell me how a model does on someone else's tasks. They do not tell me whether it will follow the exact function signature I need, or find the three real bugs in a config file. Both of those turned out to depend on the model in ways its size or price did not predict. So before I put a new model in a role, I race it against the models already in that role, on fixtures that look like my work.

The second rule is to grade by execution. An early version of my harness checked answers with regexes, and it gave false passes and false fails. A grader that imports the submitted file and calls `allow("k")` cannot be talked into a pass.

## How a race works

1. A fixture is a folder under `fixtures/tasks/`: a prompt, seed files, and a `grade.py` that prints `SCORE <0..1>`.
2. The harness copies the seed into a fresh workspace, sends the prompt to a model, and puts the returned files in the workspace.
3. The grader runs on that workspace in a separate Python process, and any model code it executes runs through `model_races/sandbox.py` (see [Security](#security)). It writes its score to a private file, so a submission that prints "SCORE 1.0" or calls `os._exit()` cannot forge a result. A submission written on purpose to attack the grader could still forge one, since it runs in the same process as the tests.
4. A grader crash, a timeout or a dead gateway gives `None`, never `0.0`. A zero means the model failed. `None` means my rig failed. The verdict step leaves `None` out of the winner math and marks confidence as `n/a`.
5. Cost and wall time are recorded next to each score. Quality, cost and time are never summed into one number.

The four fixtures:

| Fixture | Task | How it is graded |
|---|---|---|
| F1 | Fix a webhook handler that breaks when events arrive out of order | Hidden tests run against the fixed file |
| F2 | Label 20 log lines (ecc, oom, network, benign) | Accuracy against gold labels |
| F3 | Audit three small files with three planted problems | Recall of the planted problems by keyword |
| F4 | Write a plan and a stub with the exact signature `allow(key) -> bool` | The stub must import and return a bool |

There are three ways to run them:

- `worker_sweep`: one call per model per fixture, no tools. This is the one to start with. It needs only a gateway.
- `race_harness`: whole agent "heads" (Claude Code, omp) with tools, in isolated workspaces. It needs those CLIs installed.
- `tiers`: five harder code tasks with hidden tests, escalating until one separates the field. `resample` runs one tier many times and reports a pass rate.

`run_evals` is an older weekly check that grades ten short prompts with regexes. I kept it because its CSV is the longest record I have, but it is the weak method.

## Run it

You need Python 3.11 or newer and an OpenAI-compatible endpoint (LiteLLM, OpenRouter, a local llama-server).

```
uv venv .venv && uv pip install -p .venv/bin/python pytest
cp .env.example .env            # set RACE_BASE_URL and RACE_API_KEY
set -a; . ./.env; set +a
cp roster.example.toml roster.toml   # replace the role names with yours
mkdir -p runs && touch runs/spend.jsonl
.venv/bin/python -m model_races.worker_sweep F3 F4
```

`ROLES="model-a,model-b" .venv/bin/python -m model_races.worker_sweep` races two roles you name, with no roster edit.

Settings:

| Variable | Meaning |
|---|---|
| `RACE_BASE_URL` | Base URL of the gateway, default `http://localhost:4000/v1` |
| `RACE_API_KEY` | Bearer token, default `local` |
| `RACE_ROSTER` | Path to a roster file, default `roster.toml`, else `roster.example.toml` |
| `RACE_SPEND_LOG` | JSONL file of per-call cost, see below |
| `RACE_OLLAMA_URL` | Ollama URL for the `local` sweep roster |
| `RACE_LEDGER` | Optional script that records topology-race seats |
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | Auth for the Claude Code head lane, which does not read `~/.claude`. Get a token with `claude setup-token` |
| `RACE_HEAD_EXEC` | Set to `1` to let head lanes run Python (Claude Code) or a shell (omp). Off by default; see Security |

The roster file holds role names, prices, the judge models, the head lanes and the topology seats. `roster.example.toml` is the roster from my own gateway, kept as an example. Your gateway will not have those role names.

Budget caps read a spend log. Each line is `{"ts": <epoch>, "model": "...", "cost": <usd or null>}`, written by a success callback in your gateway. If the file is missing, the budget check raises an error on purpose: a missing log means the cap is blind. The `touch` above gives you an empty log, and then the cap sees zero spend. Do that only if you accept that.

Other commands:

```
.venv/bin/python -m model_races.run_evals role-a role-b
.venv/bin/python -m model_races.hard_task role-a role-b
.venv/bin/python -m model_races.resample 3 5 openrouter/some/model
.venv/bin/python -m model_races.topology_race 2 F3
BUDGET=6.00 .venv/bin/python -m model_races.race_harness F1 F2 F3 F4
.venv/bin/python -m model_races.judge runs/<stamp>/results.json
.venv/bin/python -m model_races.verdict runs/<stamp>/results.json
```

Outputs land in `runs/`, which git ignores.

## Tests

```
.venv/bin/python -m pytest -q
```

This runs offline: 37 tests pass and 1 is skipped. The skipped test calls a live judge model, so it runs only when `RACE_LIVE=1`. The tests cover the fail-closed rules: graders that cannot be forged by printing, a missing spend log, a judge that failed, a verdict that must not rank a rig failure, and a circuit breaker that stops asking a provider after one exhausted 429 backoff. They also cover the sandbox: code that reads an environment variable (directly or through `/proc`), sleeps past its timeout, spins the CPU, allocates too much memory, forks, or floods stdout is contained, and the head lanes start with a clean environment.

## What the included results showed

Everything is in `results/`. Sample sizes are small and I list them.

**Single-shot sweep, 13 cloud models, 4 fixtures, one run per cell** (`sweep-20260712-cloud-roster.json`, written up in `VERDICT-2026-07-12.md`):

- On F4, the exact-stub task, several small models passed: gpt-oss-20b, qwen3-coder-30b, gemini-2.5-flash-lite, glm-4.7-flash, claude-haiku-4.5. Three larger reasoners scored 0: glm-5.2, deepseek-v4-flash and mimo-v2.5. Their files did not expose the stated signature, or (mimo-v2.5) they returned no file. gpt-oss-120b also scored 0 there, with no file. Size did not predict this. gpt-5.6-sol and grok-4.5 also passed, at higher cost.
- On F1, every model scored 1.0, and on F2 most scored 0.95 or 1.0. Those two fixtures separate nothing.
- On F3, the audit, four models reached 0.667 (gpt-oss-120b, gpt-oss-20b, glm-5.2, gpt-5.6-sol), two reached 0.333, and the rest scored 0. No sweep model found all three planted problems.
- In the head race (Claude Code with Fable, n=1 per fixture, repeated on F4), the head scored 1.0 on F1 to F3 and 0.0 on F4 both times, because it built a class and skipped the stated function. The verdict file has the other lanes and their failures.

**Reasoning effort on F3, n=2** (`sweep-20260720-effort-run1.json`, `sweep-20260720-effort-run2.json`): the base role scored 1.0 and 1.0. The two `-hi` roles, which I set up as higher-effort versions, scored 0.667 and 0.667 in run 1, then 0.0 and 0.0 in run 2. Higher effort lost this audit task, on two runs. Two runs is a signal, not a result.

**Orchestration shapes on F3, n=2 each** (`topology-race-F3-20260720.json`): one model alone scored 1.0 then 0.0. A finder plus a verifier from another family scored 0.333 then 0.0. Three finders and a merging judge scored 0.0 and 0.0. The solo model's own two runs span the whole range, so none of these gaps is a verdict.

**Same prompt, different luck.** In back-to-back runs of one tier at one effort setting, gpt-5.6-luna scored 11/11, then 0/11. I recorded this in the docstring of `model_races/resample.py` and did not keep the raw runs. Another model went 0/10 then 10/10 before I demoted it. This is why `resample` reports a pass rate and why a single run is not a verdict.

**Local models** (`sweep-20260712-local-roster.json`): the local runs had HTTP 500 errors, empty outputs and long waits under load. I would not rank local models from that file.

`weekly-regex-results.csv` has 2,330 rows from the weekly regex check, 2026-07-08 to 2026-11-09. It includes timeouts, 429 errors and 20 junk rows from a mistaken `--help` run, all as recorded. It uses the weak grader, so I would use it for trends in latency and errors, not for quality.

## Security

This tool runs code that language models wrote. Treat every model answer as untrusted input. Run it on a machine or in a container that holds no secrets: no SSH keys, no cloud credentials, no wallets, no production access. A throwaway VM or container with only the gateway key is the right place.

What the harness does to contain model code (`model_races/sandbox.py`, used by every grader, `tiers`, `resample` and `hard_task`):

- runs it in a fresh `python -I` process, in a new temporary directory, with an environment of only `PATH`, `HOME`, `TMPDIR` and `LANG`, so keys in your shell are not passed on;
- sets resource limits before the code starts: 2 GiB of address space, 20 CPU seconds, 16 MiB per written file (stdout included), no core dumps, and no new processes;
- kills the whole process group at a wall-clock timeout;
- marks the harness process non-dumpable on Linux, so model code cannot read the harness's environment through `/proc/<pid>/environ`.

The head lanes (`race_harness`) run `claude` and `omp` with an explicit minimal environment, cwd in a per-run workspace and HOME in a fresh per-run directory beside it, which is deleted afterwards. They get file tools only (read, write, edit, search). The Claude Code lane uses `--restricted`, which keeps its file tools inside the workspace and ignores settings files. It authenticates only from `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`; it cannot use the login in your real `~/.claude`, and it refuses to start without one of those variables. The omp lane gets your `RACE_API_KEY` in a mode-0600 config file in its private HOME.

What this does not protect against:

- It is not a container. Model code runs as your user and can read every file your user can read, including the `/proc` environment of your other processes.
- There is no network isolation. Model code can open connections and send out anything it can read. Add a network namespace or firewall rule yourself if you need that.
- omp's file tools are not confined to the workspace. A head on the omp lane can read and write any path your user can.
- `RACE_HEAD_EXEC=1` gives heads Python (Claude Code) or a shell (omp). That is full code execution as your user, with the lane's credential in its environment.
- `RLIMIT_NPROC` does not apply to root, so do not run the harness as root.
- The static screen in `tiers.py` that rejects `subprocess`, `socket` and similar names is a scoring rule. It is easy to evade and is not a defence.
- Nothing here sends results anywhere. The harness talks only to `RACE_BASE_URL`, `RACE_OLLAMA_URL` and, for the head lanes, whatever the `claude` and `omp` tools contact. The judge sends head transcripts to the judge model at `RACE_BASE_URL`.

## Limits

- Four fixtures, two of which almost every model passes. F2 has 20 lines. F3 is keyword recall, so a good finding in other words scores 0.
- Most cells are one run. Where I have two runs, the runs often disagree.
- The results are from July 2026 and name models that have since changed. Prices in the roster were right on the day I wrote them.
- Model code runs with resource limits and a clean environment, not in a container. See Security.
- The judge is a model. It scores only soft traits (autonomy, verification, synthesis), and it sees transcripts with obvious product names masked, which does not fully hide the source.
- The head lanes need the `claude` and `omp` command line tools and a gateway that logs cost. The flags were checked against Claude Code 2.1.319 and omp 16.3.11; a version that rejects a flag makes the lane fail with `LANE_ERROR` rather than run with weaker settings. The omp lane expects a LiteLLM-style gateway at `RACE_BASE_URL` (it uses LiteLLM model discovery).
- The head lanes now start with a clean HOME, so they no longer load your skills, rules or MCP servers, and by default they cannot run code. The July head-race results in `results/` were measured with my skills loaded and Python allowed, so new head-race runs are not directly comparable with them.
- `VERDICT-2026-07-12.md` mentions a review folder and some gateway services that I did not publish. I also reworded two lines about other jobs on my machine.

`docs/design-spec.md` is the design note I wrote before the head-lane race, with the budget and scheduling parts removed.

## License

MIT. See `LICENSE`.
