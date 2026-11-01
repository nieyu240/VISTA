# Local API transport

This local branch adds `api` / `deepseek` as an explicit backend, backed by
`vista.backends.api.ApiSession`. The existing Codex/Claude paths still use their
original transport. API runs expose only the registered task tools; no shell,
filesystem browsing or evaluator tools are sent to the model.

## Entry points

```bash
~/.venvs/vista-benchmark/bin/vista \
  --profile gameworld --backend deepseek run \
  --game 18_minecraft-clone-glm --task 18_01 \
  --env-file /path/to/your/local.env \
  --model deepseek-flash --max-calls 110 --task-timeout 900 \
  --output /path/to/new-run
```

Equivalent: `python -m vista.localbench run --profile gameworld ...`.
Use `list`, `doctor`, `compare` and `replay` as the other commands. `doctor
--probe-model --output DIR` sends one billed image/tool request. Ordinary
`doctor` and `list` do not generate model responses.

Set `VISTA_API_KEY`, `VISTA_API_BASE` for another compatible provider, or load
them from an explicit `--env-file`. Existing DeepSeek/OpenAI variable names
are fallback choices. Credentials are not copied into manifests, request JSON,
Docker, or source files. Model request logs contain public task inputs only.

## What changes

- GameWorld and GameStore API runs reuse the upstream environment, task
  catalog, native actions, evaluation, visual inspection and memory tools.
- ARC3 runs reuse the upstream real `GameController` and tool dispatcher;
  open/close a real online scorecard, and use its returned score. Game IDs are
  resolved from the actual ARC catalog, not an invented list.
- Images come from actual environment observations after each action.
- A game action is never automatically retried. Network retries apply only to
  model requests, and are logged because a timed-out request may be billed.
- Multiple tool replies are acknowledged together before another image input;
  at most one environment action is executed from a model response. Excess
  action calls receive an explicit nonexecution reply.
- Invalid arguments and duplicate IDs pass through the runtime's validation
  without moving the environment. Assistant claims cannot set a game score.
- The host API transport uses the process proxy, avoiding the historical
  Docker proxy forwarding failure. This does not modify Codex Docker behavior.

## Experiment settings

`baseline` uses the same registered tools and image retention as `vista`.
`vista` adds an explicit hypothesis/action/observation/memory instruction.
This is a prompt comparison, not a claim that it exactly replicates the
paper's CLI agent harness. The model, provider, thinking setting, strategy,
image history, budgets and Python source hashes are preserved in manifests.

## Reliability and ablations

The local runner applies the first reliability phase from the accompanying
benchmark plan. `--reliability reliable` (the default) adds a public
`report_state` tool, whose claims must cite an episode/frame and normalized
image region. It records predictions, self-assessed visual verification,
action receipts, inspection budgets and bounded recovery notices. The tool
cannot read evaluator state or set success. `verified` omits recovery,
`ledger` only records the state card, and `legacy` disables these extensions.

`--memory-mode visual` retains the existing visual-history tools; `summary`
disables visual inspection and keeps bounded state/receipt text; `current`
starts each decision from the current observation and disables historical
memory tools; `legacy` preserves the old conversation behavior. `--inspection
off|fixed|agent` controls visual inspection exposure. The fixed option is a
reserved protocol condition and does not synthesize inspections. All these
settings, tool schema hashes, prompt hashes and the statement that private
state access is false are saved in `reliability-config.json`.

Process fields are diagnostic and separate from native scores: state-card
count, frame-reference coverage, self-assessment count, accepted action rate,
text-only turns, inspection calls/views and recovery interventions. Semantic
evidence accuracy, hidden-state accuracy, recovery success and strict success
remain `null` until an independent annotation or task constraint defines
them; they are never treated as zero or used to change the evaluator result.
Each reliability event carries an episode/event/decision ID, visibility and
monotonic timing. A replay remains a log viewer, not deterministic engine
re-execution.

The image history is explicitly bounded (default eight images). Older images
remain archived and can be revisited through `inspect`; their text/tool
references stay in the conversation. There is no unreported summary model.
Very long text histories can still reach the provider context limit; such a
failure is recorded, not converted into an artificial score.

`--thinking enabled` is sent to the official DeepSeek origin; other compatible
gateways do not receive that provider-specific parameter. Record and verify
their own reasoning configuration before claiming a matched comparison.
No API seed is assumed to guarantee deterministic model output. The recorded
seed configures GameWorld/GameStore environments; online ARC3 generation is
controlled by the remote service.

GameWorld retains the upstream 100-decision environment limit. GameStore has
an explicit `--max-steps` decision budget up to 120. Short smoke tests must
be reported separately from full-budget results. ARC3 has separate action,
model-call and wall-clock budgets. API call caps count logical requests;
transient network retries may add up to two HTTP attempts per logical call.
Wall-clock limits are checked at model-call boundaries, with request timeouts;
environment startup/cleanup and final scorecard calls add overhead.

## Evidence and results

GameWorld/GameStore use the upstream `scored` checks: completed run, valid
evaluator result, no evaluation configuration/runtime issues. Incomplete
or infrastructure-failed runs retain their evidence and remain unscored.
ARC3 stores the complete official scorecard response, game state, completed
levels, errors and termination reason. A positive official partial score
does not imply all game levels were completed.

`replay.html` is a self-contained viewer of archived observations and events,
with per-frame event matching, action/model timing and final results. It is
log replay; it does not claim deterministic re-execution of the game.

The browser Minecraft clone has collection tasks and placement controls;
its current standard tasks do not evaluate buildings or formal Minecraft
completion. Native Minecraft, Don't Starve and Terraria still require their
own installations, adapters and evaluators.

## Verification

```bash
python -m unittest discover -s tests -v
ruff check src/vista/backends/api.py src/vista/localbench.py src/vista/replay.py tests
python -m pip check
```

The runtime tests use an independent deterministic environment to verify
real state changes, image changes, evaluator-only completion, invalid action
rejection, message ordering and archival image handling. They are separate
from model/game benchmark scores.
