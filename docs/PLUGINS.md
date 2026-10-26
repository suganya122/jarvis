# Writing a chat-app plugin

A plugin lets people talk to the assistant from another app. Telegram ships with the
project (`backend/plugins/telegram/`) and is the reference implementation.

## Shape

```
my_chat/
  plugin.json      manifest (contracts/plugin.py: PluginManifest)
  __init__.py
  main.py          `python -m my_chat.main`: the process core starts
```

First-party plugins live in `backend/plugins/`. Your own go in the app data folder under
`plugins/` (for example `~/.local/share/local-assistant/plugins/my_chat/`). The folder
name must equal `name`, the entrypoint must be inside the plugin's own package, and the
major `contract_version` must match the app's. A user plugin can't reuse a first-party
plugin's name.

```json
{
  "name": "my_chat",
  "display_name": "My Chat",
  "version": "0.1.0",
  "description": "What it does, shown before the user enables it.",
  "contract_version": "1.0",
  "provides": ["channel"],
  "capabilities": ["net.fetch"],
  "entrypoint": "my_chat.main",
  "options": [{"name": "server", "label": "Server", "help": "…", "advanced": true}],
  "secrets": [{"name": "api_token", "label": "API token", "help": "Where to get it"}]
}
```

## Protocol

Core starts `python -m <entrypoint>` and talks over stdin/stdout, one JSON object per line
(`contracts/channel.py`). The process gets an environment with secrets removed. Its own
secrets arrive in the first message, read from the OS keychain.

| Direction | Message | Meaning |
|---|---|---|
| core → plugin | `channel.configure {options, secrets}` | always first |
| core → plugin | `channel.send {conversation_id, text}` | post a reply (plain text) |
| core → plugin | `channel.approval {request}` | show Approve / Deny for `request.approval_id` |
| core → plugin | `channel.approval_closed {approval_id, outcome}` | remove the buttons, show the outcome |
| core → plugin | `channel.typing {conversation_id}` | optional "typing…" indicator |
| plugin → core | `channel.ready {contract_version}` | after configure |
| plugin → core | `channel.status {state, detail}` | `connected` / `error` (shown live in Settings) |
| plugin → core | `channel.inbound {message}` | a message: `conversation_id`, `user_id`, `user_name`, `text` |
| plugin → core | `channel.decision {decision}` | a button press: `approval_id`, `user_id`, `approved` |

stdin closing means "stop". If the process dies, core restarts it (at most 5 times an
hour).

## What a plugin must do

- **Report who sent each message** (`user_id`) truthfully. Core serves only accounts the
  owner paired, and accepts a button press only from the account that was asked.
- **Only take one-to-one chats.** In a group, anyone present could talk to the computer.
- **Send plain text.** Don't turn replies into markup or links.
- **Never log request URLs or secret values.** Many APIs put the token in the URL.

## What a plugin can't do

Decide what runs. Core checks pairing, runs the request under the remote rules (anything
beyond reading asks, every time; typed commands get no "direct" pass), and the spend caps,
pause, and kill switch all apply.

A plugin is **not a sandbox**: it is a Python program running with your permissions.
Enable only plugins you trust. The app says this before enabling a third-party one.

## Other apps

- **Discord** and **Slack** fit the same shape: Discord's gateway and Slack's Socket Mode
  are outbound WebSocket connections, so like Telegram's long polling they need no public
  server. They aren't included yet.
- **WhatsApp** (Cloud API) delivers messages only to a public HTTPS webhook. That needs an
  internet-facing server or tunnel, which this project avoids, so it isn't planned.
