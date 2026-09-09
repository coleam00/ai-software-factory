# First hour

Start with the [setup prompt in the README](../README.md#set-it-up). Your coding
agent runs the installer, configures Archon and helps write your mission and journeys.
The installer selects the shared Archon source revision automatically.

1. Review the mission, especially what is out of scope.
2. Starting from a PRD instead of an app? Have the agent run `archon-backlog` on it
   and review the issues it proposes before they are published; the first one makes
   the product runnable and names its start command, so the runtime host is wired
   from it before it is built. That ticket then goes through the lifecycle like
   every other one.
3. Review the journeys and independent holdout scenarios. Have the agent configure
   runtime inputs and app startup using the installed `factory/RUNTIME_HOST.md`.
4. Have the agent run `python factory/consumer.py doctor` and
   `python factory/consumer.py list`, then explain anything still missing.
5. Watch one small issue go to a PR with `archon-ship`. For the full sequence,
   use `archon-lifecycle` with the prepared runtime/holdout inputs and merge approval.
6. Inspect the result before asking the agent to configure scheduling.

The current integration still needs live end-to-end validation. Installation checks
alone do not prove a successful factory run.

For an older installation, read the [migration guide](../template/factory/MIGRATION.md).
