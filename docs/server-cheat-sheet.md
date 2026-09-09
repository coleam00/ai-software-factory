# Server cheat sheet: every prompt, in order

The factory on a server that never sleeps, driven from your laptop's coding agent.
Everything below is a prompt you paste to the agent, except the two logins in step
6, which only a person can complete.

Any host works. Hostinger is the example because its MCP server covers the whole
setup; swap the host prompts for your provider's API, CLI or panel. Any coding
agent works: Claude Code and Codex are the two the factory has run on end to end.

Replace the placeholders before pasting:

| Placeholder | Meaning | Example |
|---|---|---|
| `<host>` | your VPS provider | `Hostinger` |
| `<vps-ip>` | the server's public IP | `203.0.113.10` |
| `<vm-id>` | the server's id in the host's panel or API | `1953721` |
| `<key-name>` | the SSH key pair name on your laptop | `factory` |
| `<you>/<app>` | the GitHub repo the factory will work in | `acme/snip` |
| `<app>` | folder and service name on the server | `snip` |
| `<domain>` | the hostname the app will be served on | `snip.example.com` |
| `<port>` | the app's local port behind the proxy | `8080` |
| `<agent>` | the coding agent the factory runs on | `Claude Code` or `Codex` |

---

## 1. Connect your agent to your host

Install your host's MCP server or CLI in the agent. Hostinger, in Claude Code:

```text
/plugin install hostinger
```

Then a read-only sanity check before trusting it with anything:

```text
What do I have on my <host> account right now? Subscriptions, domains, and any servers.
```

## 2. Generate a key and put the public half on the account

```text
Generate an SSH key pair for my factory server if I don't have one yet: ed25519, no passphrase, saved as ~/.ssh/<key-name> on this machine. Then add the public key to my <host> account under the name "<key-name>" and confirm it is listed there.
```

Works the same on macOS, Linux and Windows; OpenSSH ships with all three.

## 3. Create the server

Ubuntu 24.04 or newer, the smallest plan with 8 GB of RAM, with the key from step 2
selected at creation. Not every host API can attach a key to a running server,
which is why the key goes onto the account first. If your host's API supports
creation, it is a prompt; otherwise use the panel and note the id and IP.

```text
Create a new VPS on my <host> account: Ubuntu, the smallest plan with 8 GB of RAM, with the SSH key "<key-name>" attached. Tell me its id and IP when it is ready.
```

## 4. Lock it down, then give yourself an undo

```text
Lock down VM <vm-id> on <host>: create a firewall that only allows inbound SSH, HTTP and HTTPS, activate it on that server, then take a snapshot so I can undo anything the factory does. Confirm both.
```

Nothing in the factory needs another inbound port: the engine, the verification
host and the app listen on loopback and sit behind the proxy.

## 5. Install the toolchain over SSH

```text
SSH into my server as root@<vps-ip> with the key ~/.ssh/<key-name>. Download https://raw.githubusercontent.com/coleam00/ai-software-factory/main/bin/bootstrap-ubuntu.sh, show me what it installs, run it, and show me the tool versions it prints at the end.
```

Idempotent: run it twice and the second run changes nothing.

## 6. The two logins (you do these yourself)

Both need a browser sign-in that no agent can do for you. One SSH session:

```bash
ssh -i ~/.ssh/<key-name> root@<vps-ip>
source ~/.profile
```

### 6a. GitHub

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

`gh auth setup-git` is the command people miss: it is what lets git itself push.

### 6b. Your coding agent

**Codex** (your ChatGPT plan; no API key on the box):

```bash
codex login --device-auth
codex exec "say hi"
```

**Claude Code** (your subscription; the token is minted on your laptop and kept in
one mode-600 file that the shells and the timer source):

```bash
# on your laptop
claude setup-token
# on the server, paste the token it printed
echo 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' >> ~/.factory-env && chmod 600 ~/.factory-env
echo 'source ~/.factory-env; export IS_SANDBOX=1' >> ~/.profile && source ~/.profile
claude auth status
```

`IS_SANDBOX=1` is what lets Claude Code run unattended as root. Codex does not need it.

Then `exit`. That is the last time you type on the server.

## 7. Install the factory in your repo

```text
SSH into my server as root@<vps-ip> with the key ~/.ssh/<key-name>. Clone my repo <you>/<app> into ~/<app> with gh, then set up my AI software factory in it using https://github.com/coleam00/ai-software-factory: read its README and follow the "Instructions for the agent" section, running every command on the server over SSH. Configure Archon for <agent> (Claude Code: Sonnet for small and medium, Opus for large. Codex: GPT-6 Astra on every tier, using the codex CLI I logged in with). Help me write the mission, the journeys and the holdout for this project. When it is installed, run the doctor and list the workflows.
```

The doctor prints the pinned Archon revision and every shared workflow it can run.
It does not prove a live agent; the first lap does.

What the agent writes to `~/.archon/config.yaml` on the server, for reference:

```yaml
# Claude Code
defaultAssistant: claude
assistants:
  claude: { model: sonnet }
tiers:
  small:  { provider: claude, model: sonnet }
  medium: { provider: claude, model: sonnet }
  large:  { provider: claude, model: opus }
```

```yaml
# Codex. The engine's bundled Codex SDK is older than the newest models, so it
# points at the CLI you logged in with.
defaultAssistant: codex
assistants:
  codex:
    model: gpt-6-astra
    modelReasoningEffort: medium
    codexBinaryPath: /root/.bun/bin/codex
tiers:
  small:  { provider: codex, model: gpt-6-astra, effort: medium }
  medium: { provider: codex, model: gpt-6-astra, effort: medium }
  large:  { provider: codex, model: gpt-6-astra, effort: high }
```

## 8. Runtime verification: fresh app, private scenarios

The shared runtime and holdout workflows need a scenario file each and a host that
starts a fresh copy of the candidate on a private port. This is what makes
"verified" mean something.

```text
On the server, follow ~/<app>/factory/RUNTIME_HOST.md. Write the runtime host configuration and the scenario JSON for my journeys, plus a separate holdout scenario, under /root/private (outside the repo, mode 700). Run the runtime host as a systemd service with a private connection file, and prove one start, identity and teardown cycle by hand against a copy of main. The app must answer /build-id with the host's candidate string.
```

## 9. Watch one issue go through before any timer

A small issue, then one whole lifecycle in the foreground with the merge held for
your approval. This is the lap you watch.

```text
File a small issue on <you>/<app> for <the change you want>, then on the server, in ~/<app>, run one shared lifecycle on that issue in the foreground under nohup so it survives our session: publish triage labels, use the runtime and holdout scenarios under /root/private, merge mode approve, discovery publication preview. Follow the run and tell me when it pauses for my approval or finishes.
```

```text
Approve the held merge on that run and follow it to the end.
```

## 10. Deploy: the service, the proxy, the DNS

Merging is not shipping. The app runs as a service behind Caddy from its own
checkout of `main`; the shared `archon-deploy` workflow rolls it forward after a
merge and reads `/build-id` back.

```text
On the server, run a checkout of ~/<app>'s main as a systemd service called <app> on port <port>, enabled at boot, with its data under /var/lib/<app>, behind Caddy on <domain> (reverse_proxy to 127.0.0.1:<port>). Then run archon-deploy once with the deploy command (pull main into that checkout and restart <app>), the health URL https://<domain>/health and the identity URL https://<domain>/build-id, and show me that /build-id equals the head of main.
```

```text
Point <domain> at my VPS on <host>: add an A record for <vps-ip> with a 5 minute TTL, and tell me when it resolves.
```

## 11. Turn the timer on

One shared lifecycle run per tick, serially, with backlog intake: each tick takes
the oldest open issue nobody has touched, and a tick that finds nothing does
nothing. The unit ships with the install.

```text
On the server, schedule the factory in ~/<app> as a systemd service from factory/factory-timer.service.example: one shared lifecycle run per tick, ten minutes apart, backlog intake on (empty target, publish=true), merge mode auto, discovery publication preview, and the deploy, health and /build-id commands for the <app> service. Enable it and follow its journal.
```

From here, filing an issue is the only input.

## 12. Running it

Ask your agent for anything else: status, the last few ticks, a run's record with
the provider and model of every node, approving a held merge, resuming a run that
paused on a pending GitHub check, pausing new launches, stopping the timer. It
knows the factory's commands from the README. Two you will use:

```text
On the server, show me the factory's status: the timer's last three ticks from the journal, any active or paused runs, and the build id the live app reports.
```

```text
On the server, stop the factory timer and make sure no run is still active. Leave the app running.
```
