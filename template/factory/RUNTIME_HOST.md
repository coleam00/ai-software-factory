# Project runtime host

Factory can own ordinary application environments while Archon owns every workflow
and coding agent. It never chooses a stage, retries an agent, evaluates a scenario,
interprets workflow output to dispatch more work, or authorizes a merge.

## Foreground lifetime

```text
factory run archon-verify-runtime --runtime-host /private/runtime.json --input scenario=/private/baseline.json
```

The consumer starts a separate local owner, passes its connection through
`FACTORY_RUNTIME_URL` and `FACTORY_RUNTIME_TOKEN` to ONE native invocation, waits
for that foreground invocation, and closes the owner in `finally`. A pipe also
detects abrupt consumer exit. The owner cancels readiness/setup waits, terminates
its process trees, removes its temporary source/state directories, and exits.
The consumer waits for that exit; failed cleanup fails the command.

`--runtime-host` cannot accompany `--detach`, `-d`, or `--resume`, including equals
forms. Native durable ownership/terminal notification is not integrated. A paused
or exited foreground run loses its environments; use a fresh foreground run.
Generic factory commands without this option retain native detach/resume behavior.
Do not use host mode with a composition that internally leaves durable background
work after its foreground invocation returns.

Manual standalone usage keeps the same lifetime rule:

```text
python factory/runtime_host.py serve --config /private/runtime.json --connection-file /private/connection.json
```

Keep that process foreground while invoking native Archon separately. Project
commands use `--connection-file /private/connection.json` instead of the inherited
environment. Ctrl+C or termination cleans owned apps and removes the connection
file. Abrupt parent death also cleans apps; a leftover connection file then holds
an invalid credential and can be removed. The file is created exclusively with
Unix mode 0600. On Windows use a directory whose ACL permits only the operator;
Python mode bits do not replace Windows ACLs. Keep it outside repositories and
public artifacts. Do not put tokens in commands, scenario JSON, or reports.

## Trusted configuration (version 1)

Configuration is operator-owned JSON, loaded once by the owner. Example:

```json
{
  "version": 1,
  "roots": {
    "candidate": {
      "path": "/absolute/private/candidate",
      "binding": ".factory-resource.json"
    }
  },
  "include": ["app.py", "src"],
  "shape": "http",
  "setup": ["{python}", "src/prepare.py"],
  "command": ["{python}", "app.py"],
  "env": {"APP_DATABASE": "{state}/app.sqlite"},
  "health_path": "/health",
  "identity_path": "/build-id",
  "timeout_s": 30,
  "mutations": [
    {"id": "negative", "file": "src/store.py", "find": "unique original", "replace": "changed original"}
  ]
}
```

List only files/directories the app needs. Paths resolve under exactly the named
roots; callers cannot submit arbitrary paths or commands. Windows roots use
absolute drive paths. Relative, rooted-drive-relative, escaping and linked source
paths fail. `.factory`, `.git`, `.archon`, `.claude`, `.env`, `HOLDOUT.md`, virtual
environments, node_modules and bytecode caches are refused in included paths.
An included directory containing these fails explicitly. Do not list the whole
checkout. Other private evaluator filenames must also be excluded by the operator.

### Bind the candidate root to the delivered revision

Run preparation from the checkout Archon is delivering. Pass the trusted full
revision selected by that delivery; do not obtain it from the old candidate root:

```text
python factory/runtime_resource.py prepare --config /private/runtime.json --destination /private/candidate --expected-revision <delivered-sha>
```

The portable Python command reads `include` from the same runtime configuration,
invokes Git directly, and archives only those permitted committed application
paths. Tracked evaluator, holdout and other repository files outside that list
never enter the resource. The command records the source revision, tree, include
list and resource digest in the owned root. It
refuses modified tracked source, a revision other than the helper's own checkout
HEAD, forbidden/escaping/missing/linked includes, broad or relative destinations,
and any existing directory it did not create.
It never deletes an unowned directory. When replacing an owned resource, it keeps
the prior directory on the same filesystem and restores it if promotion of the new
resource fails. A reported preparation failure therefore preserves the last usable
candidate. No `rm`, `find`, shell pipe or platform
archive program is part of the normal path.

The helper and the workflow working directory must resolve to the same delivering
Git checkout. An absolute helper path into an old checkout is refused even when
`--expected-revision` is omitted. Automation should pass its trusted full SHA;
a mismatch is refused. The scenario cleans its slot and uses the helper's `start`
wrapper so the same delivering checkout supplies its revision at the runtime
boundary:

```json
{
  "environment": {
    "ownership": "external",
    "setup": "python factory/runtime_resource.py prepare --config /private/runtime.json --destination /private/candidate && python factory/runtime_host.py setup --slot baseline",
    "start": "python factory/runtime_resource.py start --slot baseline --root candidate",
    "teardown": "python factory/runtime_host.py teardown --slot baseline",
    "candidate_command": "python factory/runtime_host.py identity --slot baseline"
  }
}
```

Those commands use the credentials injected by Factory's foreground
`--runtime-host` mode. With a manual `serve` process, add
`--connection-file /private/connection.json` to the wrapper and host commands.

Run these commands with the delivered checkout as the workflow working directory.
Absolute executable and script paths are safe only when the script belongs to that
same checkout; never fall back to a helper in an older checkout. The host reloads
and checks the binding on every start, so an old prepared root, an explicitly stale
expected revision, or changed prepared bytes fail before behavioral verification.

`setup` is optional and runs inside the new snapshot. `command` is required.
Both accept argv arrays only, with literal `{python}`, `{source}`, `{state}` and
`{port}` substitution. Only Python/Node executables are supported; shell commands,
batch files, provider CLIs and ambient Archon commands fail. A project script can
prepare ordinary data/dependencies under its snapshot/state. Configuration and
scripts are trusted code, and must never wrap a provider or launch workflows.
Source inclusion must cover all application code; mutable code loaded from outside
the snapshot is outside this identity claim.

The app receives a small OS environment plus configured `env`. Provider credentials,
control credentials and arbitrary parent variables are not inherited. HOME and
temporary directories point at fresh state. All writable database and app data must
use `{state}` or `FACTORY_RUNTIME_STATE`; an app configured to use an external DB
does not acquire isolation merely by running here. Set up fresh external resources
only if the project can keep them inside this process/filesystem ownership model;
remote DBs, containers, services and cloud resources are currently unsupported.

## Deterministic commands for shared nodes

These commands do no evaluation and contain no workflow policy:

```text
python factory/runtime_host.py setup --slot baseline
python factory/runtime_host.py start --slot baseline --root candidate --expected-revision <delivered-sha>
python factory/runtime_host.py restart --slot baseline
python factory/runtime_host.py describe --slot baseline
python factory/runtime_host.py identity --slot baseline
python factory/runtime_host.py teardown --slot baseline
python factory/runtime_host.py start --slot negative --root candidate --expected-revision <delivered-sha> --mutation negative
```

Slots are operator-chosen alphanumeric/underscore/hyphen names. Each `start`
cleans the prior slot first, copies current exact bytes into a fresh directory,
optionally applies ONE uniquely anchored configured mutation, runs setup, freezes
and hashes source, allocates a new target/state, starts the configured command and
waits for readiness. `--expected-source <sha256>` optionally rejects stale input
bytes. A second start is always fresh, whether baseline, holdout or malformed-report
retry. A bound root also requires `--expected-revision`; legacy unbound ordinary
roots retain `--expected-source` but make no delivered-revision claim. The shared
composition decides when to issue it; there is no Python suite loop. Concurrent
cases must use distinct slots. This owner serializes requests.

For an active HTTP slot, `restart` terminates the complete owned process tree and
launches the same trusted configured command against the exact existing frozen
snapshot and state directory. It allocates a fresh loopback target and returns a
new candidate identity bound to that launch; persistent data can therefore be
tested across a real process boundary without resnapshotting or rerunning setup.
Callers provide only the slot, never a command, path, environment, or port. The
old process is fully stopped before relaunch. If relaunch, readiness, or identity
verification fails, the host removes the whole slot because the stopped instance
cannot be rolled back safely. `restart` supports HTTP slots only; finite CLI and
library executions remain single-run.

`setup` is an idempotent cleanup boundary; provisioning happens atomically in
`start`. `teardown` is idempotent and terminates descendants before deleting files.
Authenticated malformed/failed requests clean all active slots, fail closed, and
return a generic error without echoing commands, secrets or evaluator contents.
Unauthenticated requests return 403 without disturbing running apps.

`start` and `describe` return JSON with `version`, `slot`, `candidate`,
`source_digest` (post-setup/mutation included bytes), `input_digest` (included
bytes before project setup),
`shape`, `target`, `exit_code`, `snapshot`, and `state`. `identity` prints only the
candidate string for the producer's `candidate_command`. The local control API
uses POST `/setup`, `/start`, `/restart`, `/identity`, `/teardown` with these CLI fields as JSON
and `Authorization: Bearer <private token>`; `/identity` returns full typed JSON.
The control URL is IPv4 loopback only. Requests are bounded; redirects are refused.

For PR3227's external environment contract, set `environment.ownership` to
`external`, map setup/start/teardown to the commands above, and set
`candidate_command` to `identity --slot <case>`. Use absolute, correctly Bash-quoted
executable/script paths when Archon changes working directory. Commands are fixed
trusted project strings; never interpolate caller-provided text into shell syntax.
Bound results also map the input through `source_revision`, `source_tree` and
`resource_digest`. The producer's current start node logs command output rather than returning target
JSON to the verifier. Project assertion data can tell the verifier to read
`describe --slot <case>` to discover its target; the shared suite may instead map
typed data natively. Do not hardcode the ephemeral port. An expected candidate
must come from this attempt; builder Git HEAD is not a target identity.

Keep original `harness/END-TO-END.md`, `.factory/holdout/HOLDOUT.md`, and defects JSON
unchanged. Convert assertions and case metadata into separate project JSON as
data only. Store private holdout scenario JSON outside the builder checkout;
do not include it in app snapshots. Factory does not generate shared instructions.
### Calibrate the evaluator before relying on it

Author assertions around substantive user behavior: make the user create or earn
a nonempty value, take a real transition, and verify the resulting nonzero value
and visible state. Do not seed the final state directly or accept only "the page
loads". Configure one uniquely anchored mutation that breaks that behavior.

For example, adapt this to the product's public interface in both controls:

```json
{
  "assertions": [
    {
      "id": "earned-value",
      "description": "Begin with fresh state, create one real user record through the public interface, perform the user action that earns value, then observe exactly one record, a nonempty display value, and an earned numeric value greater than zero. Do not write the database or seed the resulting state directly."
    }
  ],
  "environment": {
    "ownership": "external",
    "candidate_command": "python factory/runtime_host.py identity --slot baseline --connection-file /private/connection.json"
  }
}
```

Start stable baseline and mutation slots, record their exact host identities, and
write a caller-owned suite manifest accepted by `archon-verify-runtime-suite`:

```json
{
  "baseline": "baseline",
  "cases": [
    {"id": "baseline", "scenario": "baseline.json", "candidate": "<baseline identity>", "expected_verdict": "verified"},
    {"id": "relevant-fault", "scenario": "relevant-fault.json", "candidate": "<mutation identity>", "expected_verdict": "failed"}
  ]
}
```

The two scenarios probe those already-running slots and apply the same behavioral
expectations; the fault description identifies it as a deliberate control. Run:

```text
python factory/consumer.py run archon-verify-runtime-suite --input manifest=/private/calibration/manifest.json
```

Require `expectations_passed: true` and `baseline_verified: true`. Do not create
native return files yourself. Separately run `archon-verify-runtime` with the
baseline scenario and a deliberately different `candidate` input; require its
native verdict to be `inconclusive`. The pinned suite manifest schema permits only
`verified` and `failed` expectations, so the identity/infrastructure control
cannot truthfully be an expected third suite case. An unavailable case never
counts as a caught mutation.

Keep independent holdout scenarios private and unchanged by calibration. Repeat
this calibration at initial setup and after a material evaluator, assertion,
runtime-adapter or identity-contract change, not after prose-only edits.

Save the probe command's actual stdout/stderr and exit status, then inspect the
saved file for the literal measurements and candidate identity before accepting
it as evidence. On Windows, `Start-Transcript` can capture terminal prompts
instead of subprocess output; a nonempty transcript alone is not proof of the
observations. Capture subprocess output directly. If recovering an original tool
receipt, preserve its provenance and the original artifacts without inventing
measurements or presenting a replay as the original attempt.

## Identity and supported shapes

HTTP apps must implement a build-id path that returns the exact
`FACTORY_RUNTIME_CANDIDATE` environment string. Health and identity must both
return 200, without redirects. The candidate hashes the frozen source bytes,
resolved launch argv and environment, including unique state/target allocation.
Every probe checks the owned command is alive, source still matches, and the
specific target returns that ID. A stale/dead/wrong target fails. This is an app
cooperation contract, not a proxy claiming an arbitrary localhost server is this
candidate. Port allocation races fail identity/readiness; the host never kills an
unrelated listener. Source chmod plus rehash detects changes; it is not an
OS-enforced immutable store against a same-user adversary.

`cli` and `library` support finite configured Python/Node argv (a library needs a
project adapter script). Start executes it once, waits for completion, cleans its
descendants and returns the actual exit code and candidate binding, with null
target. Identity attests that execution's snapshot/command, not a continuing
server. It does not infer assertion success from exit zero. Interactive CLI and
arbitrary agent-supplied library calls are unsupported. State-preserving restart
is limited to an already active owned HTTP slot. Ordinary app stdout/stderr is
withheld to prevent accidental secret
publication; project adapters can write measurement files under returned `state`
for native verifier tool access until teardown.

Windows commands are assigned to a kill-on-close Job Object before the private
worker permits project execution. Cleanup waits for zero active job processes.
Unix uses a separate session/process group and kills the group on cleanup, even
after the initial command exits. Unix project commands must stay in that session;
daemonization, `setsid` escape and service managers are unsupported. This is not a
filesystem sandbox for a same-user agent. Trusted scripts/apps can deliberately
escape the stated file/identity boundaries. Machine shutdown or killing the owner
itself can leave temporary files; Windows job handles still close and kill apps.
Normal completion, failed setup/start, malformed requests, cancellation and parent
termination are covered by the independent owner. Do not claim host-machine-loss
recovery or hostile-process containment.

## Mutation evidence

```text
python harness/mutations/run.py score-runtime --result /private/native-return.json --candidate <attempt-identity>
```

This consumes the actual shared runtime return: `verified`, `verdict`, `candidate`,
`checkout`, and `summary`. A bound `failed` verdict scores CAUGHT, `verified` scores ESCAPED;
missing/inconsistent/mismatched/inconclusive results remain INCONCLUSIVE. Shared
agent attribution and assertion evidence remain in native suite artifacts. The
caller must supply the trusted native return; JSON alone is not a signed receipt.
No model exit code or synthetic `[PASS]` becomes mutation evidence. Legacy `score`
remains limited to supplied ordinary check logs.

Repository checks: `python bin/test_runtime_resource.py`, `python bin/test_runtime_host.py`, `python bin/test_consumer.py`,
the installed selftests, `python bin/audit.py`, and `python bin/selfcheck-mutations.py`.
Real subprocess fixtures verify local ownership; they do not establish live
provider attribution or a final compatible source pin.
