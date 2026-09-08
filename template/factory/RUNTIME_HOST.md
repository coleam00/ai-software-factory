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
  "roots": {"candidate": "/absolute/approved/worktree"},
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
python factory/runtime_host.py start --slot baseline --root candidate
python factory/runtime_host.py describe --slot baseline
python factory/runtime_host.py identity --slot baseline
python factory/runtime_host.py teardown --slot baseline
python factory/runtime_host.py start --slot negative --root candidate --mutation negative
```

Slots are operator-chosen alphanumeric/underscore/hyphen names. Each `start`
cleans the prior slot first, copies current exact bytes into a fresh directory,
optionally applies ONE uniquely anchored configured mutation, runs setup, freezes
and hashes source, allocates a new target/state, starts the configured command and
waits for readiness. `--expected-source <sha256>` optionally rejects stale input
bytes. A second start is always fresh, whether baseline, holdout or malformed-report
retry. The shared composition decides when to issue it; there is no Python suite
loop. Concurrent cases must use distinct slots. This owner serializes requests.

`setup` is an idempotent cleanup boundary; provisioning happens atomically in
`start`. `teardown` is idempotent and terminates descendants before deleting files.
Authenticated malformed/failed requests clean all active slots, fail closed, and
return a generic error without echoing commands, secrets or evaluator contents.
Unauthenticated requests return 403 without disturbing running apps.

`start` and `describe` return JSON with `version`, `slot`, `candidate`,
`source_digest` (post-setup/mutation bytes), `input_digest` (original input bytes),
`shape`, `target`, `exit_code`, `snapshot`, and `state`. `identity` prints only the
candidate string for the producer's `candidate_command`. The local control API
uses POST `/setup`, `/start`, `/identity`, `/teardown` with these CLI fields as JSON
and `Authorization: Bearer <private token>`; `/identity` returns full typed JSON.
The control URL is IPv4 loopback only. Requests are bounded; redirects are refused.

For PR3227's external environment contract, set `environment.ownership` to
`external`, map setup/start/teardown to the commands above, and set
`candidate_command` to `identity --slot <case>`. Use absolute, correctly Bash-quoted
executable/script paths when Archon changes working directory. Commands are fixed
trusted project strings; never interpolate caller-provided text into shell syntax.
The producer's current start node logs command output rather than returning target
JSON to the verifier. Project assertion data can tell the verifier to read
`describe --slot <case>` to discover its target; the shared suite may instead map
typed data natively. Do not hardcode the ephemeral port. An expected candidate
must come from this attempt; builder Git HEAD is not a target identity.

Keep original `harness/END-TO-END.md`, `.factory/holdout/HOLDOUT.md`, and defects JSON
unchanged. Convert assertions and case metadata into separate project JSON as
data only. Store private holdout scenario JSON outside the builder checkout;
do not include it in app snapshots. Factory does not generate shared instructions.
The final suite manifest and live Allot cases remain integration work for the root
owner, alongside the final producer pin.

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
server. It does not infer assertion success from exit zero. Interactive CLI,
arbitrary agent-supplied library calls and restarts with preserved state are
unsupported. Ordinary app stdout/stderr is withheld to prevent accidental secret
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

Repository checks: `python bin/test_runtime_host.py`, `python bin/test_consumer.py`,
the installed selftests, `python bin/audit.py`, and `python bin/selfcheck-mutations.py`.
Real subprocess fixtures verify local ownership; they do not establish live
provider attribution or a final compatible source pin.
