# Server cheat sheet: every prompt, in order

The factory on a server that never sleeps, driven from your laptop's coding agent.
Almost everything below is a prompt you paste to the agent; it runs the commands
over SSH and through your host's MCP server or CLI. You touch the server yourself
for exactly two logins, and even those the agent can start for you.

Any host works. Hostinger is the example because its MCP server covers the whole
setup; swap the host prompts for your provider's API, CLI or panel. Any coding
agent works: Claude Code and Codex are the two the factory has run on end to end.

**Placeholders.** Replace before pasting:

| Placeholder | Meaning | Example |
|---|---|---|
| `<host>` | your VPS provider | `Hostinger` |
| `<vps-ip>` | the server's public IP | `203.0.113.10` |
| `<vm-id>` | the server's id in the host's panel/API | `1953721` |
| `<key-name>` | the SSH key pair name on your laptop | `factory` |
| `<you>/<app>` | the GitHub repo the factory will work in | `acme/snip` |
| `<app>` | folder and service name on the server | `snip` |
| `<domain>` | the hostname the app will be served on | `snip.example.com` |
| `<port>` | the app's local port behind the proxy | `8080` |
| `<agent>` | the coding agent the factory runs on | `Claude Code` or `Codex` |

Commands shown under "by hand" are what the prompt makes the agent run; you never
need them unless you prefer typing.

---

## 1. Connect your agent to your host

Install your host's MCP server or CLI in the agent. Hostinger, in Claude Code:

```text
/plugin install hostinger
```

Then a read-only sanity check before trusting it with anything:

> What do I have on my <host> account right now? Subscriptions, domains, and any servers.

## 2. Generate a key and put the public half on the account

> Generate an SSH key pair for my factory server if I don't have one yet: ed25519, no passphrase, saved as ~/.ssh/<key-name> on this machine. Then add the public key to my <host> account under the name "<key-name>" and confirm it is listed there.

By hand:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/<key-name> -N ""
```

Works the same on macOS, Linux and Windows (OpenSSH ships with Windows; the path
is `~\.ssh\<key-name>` in PowerShell).

## 3. Create the server

Create it in the host's panel or, where the host's API supports it, by prompt: Ubuntu
24.04 or newer, pick the key you just uploaded, the smallest plan with 8 GB of RAM.
Attaching a key to an already-running server is not something every host API can
do, which is why the key goes onto the account first and is selected at creation.
Note the id and the IP.

> Create a new VPS on my <host> account: Ubuntu, the smallest plan with 8 GB of RAM, with the SSH key "<key-name>" attached. Tell me its id and IP when it is ready.

## 4. Lock it down, then give yourself an undo

> Lock down VM <vm-id> on <host>: create a firewall that only allows inbound SSH, HTTP and HTTPS, activate it on that server, then take a snapshot so I can undo anything the factory does. Confirm both.

Nothing in the factory needs an inbound port beyond those three: the engine, the
verification host and the app itself listen on loopback and sit behind the proxy.

## 5. Install the toolchain over SSH

> SSH into my server as root@<vps-ip> with the key ~/.ssh/<key-name>. Download https://raw.githubusercontent.com/coleam00/ai-software-factory/main/bin/bootstrap-ubuntu.sh, show me what it installs, run it, and show me the tool versions it prints at the end.

By hand:

```bash
ssh -i ~/.ssh/<key-name> root@<vps-ip>
curl -fsSL https://raw.githubusercontent.com/coleam00/ai-software-factory/main/bin/bootstrap-ubuntu.sh | bash
source ~/.profile
```

Idempotent: run it twice and the second run changes nothing.

## 6. The two logins

GitHub and your coding agent both need a browser once. The agent can start each
login on the server, read the one-time code out of the output and show it to you;
you approve it in your browser.

**GitHub** (device code, then the command people miss so git itself can push):

> On the server, run `gh auth login --hostname github.com --git-protocol https --web`, show me the one-time code and the URL, and wait until it completes. Then run `gh auth setup-git` and prove `gh auth status` is logged in.

**Codex** (your ChatGPT plan; no API key on the box):

> On the server, run `codex login --device-auth`, show me the code and the URL, wait for it to complete, then run `codex exec "say hi"` to prove it works.

**Claude Code** (your subscription; the token is minted on your laptop and stored in
one mode-600 file the shells and the timer source):

> Run `claude setup-token` here on my laptop and follow it to get a token. Then on the server, write `export CLAUDE_CODE_OAUTH_TOKEN=<that token>` into ~/.factory-env with mode 600, append `source ~/.factory-env; export IS_SANDBOX=1` to ~/.profile, and prove `claude auth status` on the server reports logged in. Never print the token back to me or commit it anywhere.

`IS_SANDBOX=1` is what lets Claude Code run unattended as root. Codex does not need it.

By hand, if you would rather not have the token pass through the agent:

```bash
# laptop
claude setup-token
# server
echo 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' >> ~/.factory-env && chmod 600 ~/.factory-env
echo 'source ~/.factory-env; export IS_SANDBOX=1' >> ~/.profile && source ~/.profile
claude auth status
```

Optional: `gh auth login -s workflow` if the factory should ever be allowed to change
files under `.github/workflows/`; the default scopes cannot push those.

## 7. Install the factory in your repo

The README's setup prompt, with the server in front of it. The agent clones your
repo, installs the pinned engine and shared workflows, configures the provider and
tiers, and writes the three files with you.

> SSH into my server as root@<vps-ip> with the key ~/.ssh/<key-name>. Clone my repo <you>/<app> into ~/<app> with gh, then set up my AI software factory in it using https://github.com/coleam00/ai-software-factory: read its README and follow the "Instructions for the agent" section, running every command on the server over SSH. Configure Archon for <agent> (Claude Code: Sonnet for small and medium, Opus for large. Codex: GPT-6 Astra on every tier, using the codex CLI I logged in with). Help me write the mission, the journeys and the holdout for this project. When it is installed, run the doctor and list the workflows.

By hand:

```bash
git clone https://github.com/coleam00/ai-software-factory ~/ai-software-factory
gh repo clone <you>/<app> ~/<app>
cd ~/<app> && python3 ~/ai-software-factory/bin/factory.py init
python3 factory/consumer.py doctor
python3 factory/consumer.py list
```

The doctor prints the pinned Archon revision and every shared workflow it can run.
It does not prove a live agent; the first lap does.

Provider configuration lives in `~/.archon/config.yaml` on the server:

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
# Codex (the engine's bundled Codex SDK is older than the newest models, so point
# it at the CLI you logged in with)
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
starts a fresh copy of the candidate on a private port. This is the part that
makes "verified" mean something, and it is wiring the agent does from the installed
`factory/RUNTIME_HOST.md`.

> On the server, follow ~/<app>/factory/RUNTIME_HOST.md. Write the runtime host configuration and the scenario JSON for my journeys, plus a separate holdout scenario, under /root/private (outside the repo, mode 700). Run the runtime host as a systemd service with a private connection file, and prove one start, identity and teardown cycle by hand against a copy of main. The app must answer /build-id with the host's candidate string.

## 9. Watch one issue go through, by hand, before any timer

File a small issue, then run the whole lifecycle once in the foreground with the
merge held for your approval. This is the lap you watch.

```bash
gh issue create -R <you>/<app> --title "..." --body "..."
```

> On the server, in ~/<app>, run one shared lifecycle on https://github.com/<you>/<app>/issues/<n> in the foreground under nohup so it survives our session: publish triage labels, use the runtime and holdout scenarios under /root/private, merge mode approve, discovery publication preview. Follow the run and tell me when it pauses for my approval or finishes.

By hand:

```bash
cd ~/<app>
python3 factory/consumer.py run archon-lifecycle --branch factory/issue-<n> \
  --input target=https://github.com/<you>/<app>/issues/<n> --input publish=true \
  --input scenario=/root/private/scenario.json --input holdout=/root/private/holdout.json \
  --input merge_mode=approve --input discovery_publication=preview \
  "Take this issue through the factory"
python3 factory/consumer.py status --all --json           # find the run id
python3 factory/consumer.py approve <run-id> --comment "Approved"
```

## 10. Deploy: the service, the proxy, the DNS

Merging is not shipping. The app runs as a service behind Caddy from its own
checkout of `main`; the shared `archon-deploy` workflow rolls it forward after a
merge and reads `/build-id` back.

> On the server, run a checkout of ~/<app>'s main as a systemd service called <app> on port <port>, enabled at boot, with its data under /var/lib/<app>, behind Caddy on <domain> (reverse_proxy to 127.0.0.1:<port>). Then run archon-deploy once with the deploy command (pull main into that checkout and restart <app>), the health URL https://<domain>/health and the identity URL https://<domain>/build-id, and show me that /build-id equals the head of main.

> Point <domain> at my VPS on <host>: add an A record for <vps-ip> with a 5 minute TTL, and tell me when it resolves.

By hand:

```bash
python3 factory/consumer.py run archon-deploy --branch factory/deploy \
  --input 'deploy=git -C /srv/<app> pull --ff-only origin main && systemctl restart <app>' \
  --input 'health=curl -fsS https://<domain>/health' \
  --input 'identity=curl -fsS https://<domain>/build-id' "Deploy main"
curl https://<domain>/build-id
```

## 11. Turn the timer on

One shared lifecycle run per tick, serially, with backlog intake: each tick takes
the oldest open issue nobody has touched, and a tick that finds nothing does
nothing. The unit ships with the install.

> On the server, schedule the factory in ~/<app> as a systemd service from factory/factory-timer.service.example: one shared lifecycle run per tick, ten minutes apart, backlog intake on (empty target, publish=true), merge mode auto, discovery publication preview, and the deploy, health and /build-id commands for the <app> service. Enable it and follow its journal.

By hand: fill in `.factory/schedule.json`, copy the example unit to
`/etc/systemd/system/factory-timer.service` with the three placeholders filled, then
`systemctl daemon-reload && systemctl enable --now factory-timer`.

From here, `gh issue create` is the only input.

## 12. Watching, pausing, stopping

```bash
journalctl -u factory-timer -f                       # the live view
python3 factory/consumer.py status --all --json     # active and paused runs
python3 factory/consumer.py get <run-id> --json --verbose --events   # one run's record, with provider/model per node
python3 factory/consumer.py approve <run-id>        # answer a held merge (approve mode)
python3 factory/consumer.py resume <run-id>         # continue a run paused on a pending GitHub check
python3 factory/consumer.py halt                    # brake: no new launches (unhalt clears it)
python3 factory/consumer.py cancel <run-id>         # stop an active run
systemctl disable --now factory-timer               # stop the timer; the app stays up
```

> On the server, show me the factory's status: the timer's last three ticks from the journal, any active or paused runs, and the build id the live app reports.

## Order of operations, if you are recording it

Steps 1-6 are the one-time human-adjacent part. Step 7 installs. Step 8 is best done
before an audience: it is real work. Step 9 is the lap you watch. Do step 10 before
step 11 (the timer's deploy inputs refer to the service). Step 11 is the last thing
you turn on, and the first thing you turn off if anything looks wrong.
