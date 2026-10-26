# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's **"Report a vulnerability"**
button (Security → Advisories) on this repository. Do not open a public issue.

Include the affected version, steps to reproduce, and the impact. We aim to reply
within 7 days and to ship a fix or mitigation within 30 days for serious issues.

## Scope

In scope:

- The local daemon: auth bypass, Host/Origin bypass, cross-site access from a web page,
  secret leakage (API key, auth token) into logs, files, or responses.
- Prompt injection that makes the assistant take an action without the required approval.
- Escaping the folder confinement or the command sandbox (`run_command`), or reaching the
  app's own data folder through a tool.
- The web UI, for example XSS.
- Chat-app plugins (Telegram): getting served without pairing, answering someone else's
  approval, making a change from a chat app without an approval, or leaking a bot token.

Out of scope: attacks that need an attacker already running code as your OS user
(including a malicious plugin you installed yourself: plugins are not sandboxed), and
problems in the Anthropic API or a chat service itself.

See the "Security model" section of the [README](README.md) for the design.
