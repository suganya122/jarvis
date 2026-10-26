# Architecture (original design)

Goal: a lean, cross-platform (macOS, Windows, Linux) desktop assistant. Fast voice, reliable computer control, self-learning skills, very low running cost (Claude API is the only paid part).

Clean-room note: this design was written from an understanding of the problem space and a high-level study of how existing MIT-licensed assistants are organised. No code, prompts, schemas, text, or assets were copied. Claude Code must design all implementation details itself and may improve on this document (record changes in `docs/DECISIONS.md`).

## 1. What we learned (in our own words)

| Observation about existing projects | Lesson for us |
|---|---|
| One is a large, general platform: a central control service, dozens of chat channels, 100+ provider and feature plugins, multi-user, hosted options. Very powerful, very heavy. | Do not chase breadth. Stay small and sharp: desktop control, voice, skills, cost. Add breadth only through a tiny plugin contract. |
| The other is a single-OS desktop assistant with great voice and "learn once, replay free" behaviour, but it lives in two giant files and is tied to one OS and one model client. | Keep its good ideas (fast path, learning, verified actions), but use small modules, OS-neutral interfaces, and a model-agnostic core. |
| Heavy platforms accumulate config compatibility layers, huge test surfaces and long boot paths. | Strict, versioned contracts; one config schema; fast startup budget; no silent legacy aliases. |

## 2. Principles
1. **Cheap by construction**: every request goes down a ladder: skill replay (free) -> rule router (free) -> small model -> bigger model. The LLM is the last resort, not the default.
2. **Verify, don't assume**: every action returns a checked outcome. Unverified outcomes are never learned.
3. **Small contracts, swappable parts**: STT, TTS, model, capture, hands, channel are interfaces with fake implementations for tests.
4. **Crash isolation**: OS-touching code runs in separate worker processes; a crash there never kills the core.
5. **Least privilege**: each capability (screen, input, shell, files, network) is separately permissioned, logged, and revocable.
6. **Observable**: every request has a trace id and a latency/cost breakdown.

## 3. Process model

```
 Chat window (web UI) ──────┐  owns the mic + chat history
 Voice overlay (Tk) ────────┤  mirrors voice requests; Stop, Approve/Deny (stdin/stdout)
 Chat apps (plugins) ───────┤  Telegram, … (stdin/stdout)
 Desktop shortcuts (CLI) ───┤  talk / dictate / stop / approve / deny (HTTP)
                            ▼
                    ┌─────────────┐
                    │  CORE       │  agent loop, router, skills, memory, policy, cost, audit
                    └──┬───┬───┬──┘
        ┌──────────────┘   │   └───────────────┐
        ▼                  ▼                   ▼
  Model workers      Device workers       Voice workers
  (Claude, local)    (hands, capture,     (STT, VAD, TTS)
                      windows, browser)
```

- Core: single async process (Python 3.12, asyncio, strict typing, pydantic models for every message). Owns state and policy; does no OS-specific work itself.
- Device workers: one process per capability family (hands, capture). Talk to core over a local socket using the same typed message contracts. Restartable by a supervisor.
- Voice workers: audio capture, VAD, STT, TTS in their own process so audio never blocks the loop.
- UI: TypeScript SPA served by core on 127.0.0.1, token + Origin checks.

### UI surfaces: one session, two windows

- **Chat window** (the web UI) is the main surface. It owns the warm microphone stream
  and the chat. A voice turn is an ordinary chat turn in the window's current session, so
  it lands in chat history like a typed one, and any reply can be read aloud (automatically
  after spoken requests, optionally after typed ones, or with **Read aloud**).
- **Voice overlay** (`workers/overlay`, Tk from the standard library) is a small window
  that appears over whatever app you're in while a voice request runs:
  - it shows listening / thinking / working / done, the live transcript, a one-line
    status, inline Approve/Deny, and Stop, then hides itself;
  - it's always on top, never takes focus by itself, and opens top-centre of the display
    the pointer is on.
  - **Ownership:** core's `OverlayController` holds all overlay state, fed by every
    message the chat window is sent, and drives the window over stdin/stdout. The window
    only draws and reports button presses. Approvals answered in either surface (or by
    the `approve` / `deny` desktop shortcuts) resolve the same pending request.
  - **Fallback:** if there's no display or no Tk, the overlay reports "unavailable" and
    the chat window does everything.

### Active context ("this", "here")

1. **At the hotkey:** the hands worker snapshots `ActiveContext` (`contracts/context.py`):
   the displays (`workers/common/displays.py`: Win32, CoreGraphics or xrandr, with scale
   and negative origins), the pointer, the focused window and control (AT-SPI on Linux),
   the selection, the clipboard if enabled, the blocklisted app in focus if any, and what
   couldn't be determined.
2. **Pinning:** core's `ContextService` gives the snapshot to the next voice request and
   pins it to that chat (`ChatPermissions.context`).
3. **The model's view:** it gets a context block next to memory, after the cached system
   prompt.
4. **Enforcement:** the executor's guard keeps `capture_screen` on that display unless the
   user names another, and corrections retarget and re-run the last voice request.

## 4. Core contracts (interfaces, not implementations)
- `ModelProvider`: stream(messages, tools, tier) -> events; reports tokens and cost.
- `SpeechToText`: stream(audio) -> partial/final transcript events.
- `TextToSpeech`: speak(text) -> cancellable playback.
- `Hands`: list_windows, focus, click, type, key, scroll, menu; each returns `ActionResult{ok, evidence, state_after}`.
- `Capture`: screenshot(target), read_text(target) via accessibility tree.
- `Channel`: receive(), send(), request_approval() (chat-app plugins, see docs/PLUGINS.md).
- `OverlaySurface`: show(view), hide() (the voice overlay; a fake in tests).
- `ActiveContext` / `Display`: the hotkey snapshot; `Hands` gains focused_element,
  selected_text and focused_blocked_app (Linux: AT-SPI; FakeHands for tests).
- `Browser`: open, snapshot, click, fill, select, back, show (hand over), close. Playwright
  for real (`workers/browser`), `FakeBrowser` in tests; safety rules are pure functions over
  a `PageSnapshot` (`workers/browser/safety.py`).
- `Tool`: name, JSON schema, risk level, async run(); registered by core or plugins.
- `Skill`: parameterized recorded plan plus verification checks.

All contracts are versioned; a plugin declares the contract version it targets.

## 5. Request pipeline (the cost and latency ladder)
1. **Intake**: text or final voice transcript becomes a `Request` with a trace id.
2. **Skill match**: embedding-free matching first (normalized intent + parameter extraction); replay with no LLM if confidence is high and its verification checks pass.
3. **Rule router**: deterministic commands (open app, move window, media, search).
4. **Small model** with cached system prompt and a compact tool list; may answer or call tools.
5. **Escalation**: on tool failure, low confidence, or multi-step plan, retry once on the larger tier.
6. **Policy gate** before every tool call: risk level, permissions, approval if needed.
7. **Verify** the result; on success feed the learner; on failure repair or report honestly.
8. **Respond** (stream text, optional speech) and write the audit + cost record.

## 6. Skill learner
- Record the sequence of verified tool calls for a successful request.
- Abstract parameters (app names, text, numbers) by diffing across runs or by asking the model once to name them.
- Store a skill = trigger phrases + parameter slots + steps + postconditions.
- Promote after N verified successes; demote or flag when a postcondition fails; always user-visible and editable.
- Skills never contain secrets; destructive steps always re-ask on replay.

## 7. Voice pipeline
- Warm mic stream -> VAD -> streaming STT emitting partials -> final transcript on release/silence.
- Speculative prefetch on partials (model call may start, no tool runs until final text is confirmed and materially unchanged).
- Per-stage timers exported as metrics; CI gate on regressions.
- Dictation is a separate mode with its own tiny cleanup step and a "never drop content" guard.
- Hands-free: the **ears** process (`workers/ears`, PortAudio via sounddevice) owns the microphone and speaker and streams 100 ms blocks to core. `core/hands_free.py` waits for the wake word using the voice worker's speech-to-text, runs the request as a normal chat turn, speaks the reply (Piper if installed, else the OS voice), and listens for follow-ups. Nothing heard is kept unless it starts with the wake word.
- The overlay (a separate Tk process) receives views plus a loudness number (`overlay.level`, about 12 Hz while listening) computed in core from the mic chunks. Audio never goes to it. Its orb reacts to the level and respects the OS reduce-motion setting.

## 8. Security model
- Capabilities: `screen.read`, `input.control`, `shell.run`, `fs.read`, `fs.write`, `net.fetch`, `send.external`, `purchase`. Each has a policy: allow, ask, deny; per app and per session overrides.
- Untrusted-content rule: anything from screens, web pages, files, or messages is tagged untrusted and cannot grant itself permissions or trigger risky tools.
- Local server hardening: loopback only, token, Host/Origin validation, rate limits.
- Secrets in the OS keychain; redaction in logs.
- Sandboxed shell with allowlist; global kill switch and stop hotkey.

## 9. Data
- SQLite with migrations: settings, memory, skills, audit, cost ledger, sessions.
- Append-only audit log; cost ledger drives spend caps.
- Everything exportable and deletable.

## 10. Plugins (kept tiny)
- A plugin is a folder with a manifest (name, contract versions, capabilities requested) and a module exposing tools, a channel, or a provider.
- Runs in a separate process by default; capabilities are declared up front and approved by the user.
- First-party extras (extra channels, meeting mode) use the same mechanism.

## 11. Repo layout
```
core/        agent loop, router, policy, cost, audit (no OS code)
contracts/   typed message and interface definitions (the source of truth)
workers/     os_tools/, hands/, voice/, overlay/, browser/ with per-OS backends + fakes
models/      provider adapters
skills/      learner, store, replay, verifier
ui/          web app
plugins/     first-party example plugins
tests/       unit, contract, integration (fake OS), evals, latency
docs/        ARCHITECTURE.md, DECISIONS.md, SECURITY.md
```
Rules: no file over ~400 lines; core never imports from workers; workers never import from core; only `contracts/` is shared.

## 12. Quality gates
- Contract tests that every backend (real and fake) must pass.
- Scenario evals: task success rate, skill replay reliability, cost per task.
- Latency budget tests; startup-time budget.
- Strict type checking, lint, dependency audit, license check in CI.

## 13. Where we aim to be better, and the honest limits
Aim: smaller and easier to audit, genuinely cross-platform from day one, cheaper per task, safer defaults, clearer errors.
Limits: the other projects have large head starts in polish, integrations, users, and testing time. We win by focus and design quality, not by feature count, and we must prove it with the evals above rather than claim it.
