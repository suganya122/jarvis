# Decisions

Changes to, and choices made within, [ARCHITECTURE.md](ARCHITECTURE.md). Newest last.

## D1. Core language: Python 3.12

The architecture already assumes Python (asyncio, pydantic contracts). Python also has
the strongest free local-ML stack we need later (faster-whisper, sherpa-onnx, Silero VAD,
Kokoro), plus `keyring` and per-OS accessibility bindings (pyobjc, comtypes/UIA,
pyatspi). TypeScript would force a second runtime for the voice and vision workers.
Cost: packaging is harder than a Node binary. We will address that in milestone 9.

## D2. Repo split into `backend/` and `frontend/`

The user asked for separate backend and frontend folders. The Part 2 layout is kept
inside `backend/` (`contracts/`, `core/`, `models/`, `workers/`, `skills/`, `plugins/`,
`tests/`). `ui/` became `frontend/` at the top level. `docs/` stays at the root.
Python dependencies live in a virtualenv at `backend/.venv`, Node dependencies in
`frontend/node_modules`.

## D3. Web stack: FastAPI + Uvicorn, React + Vite + TypeScript

FastAPI (MIT) gives typed request validation from the same pydantic models used for
contracts. The security layer is plain ASGI middleware, so we are not tied to FastAPI
features. React (MIT) and Vite (MIT) are the most widely known SPA tools, which makes
contributing easier.

## D4. Auth token delivery: URL fragment + WebSocket subprotocol

- The token is generated at first run (32 random bytes), stored with mode 0600 in the
  app-data folder, and printed to the terminal as `http://127.0.0.1:PORT/#token=…`.
- The fragment is never sent to the server, so the token does not reach access logs.
  The SPA moves it into `sessionStorage` and strips it from the URL.
- HTTP uses `Authorization: Bearer`. Browsers can't set headers on WebSockets, so the
  client offers two subprotocols, `assistant.v1` and `token.<token>`. The server checks
  the token and echoes only `assistant.v1`.
- Every request and WebSocket is checked for Host (blocks DNS rebinding) and Origin (if
  present). No CORS headers are sent. A strict CSP is set. Requests are rate-limited.
- `/api/health` is the only unauthenticated API route.

## D5. Model tiers and model ids are config, not code

Defaults (editable in settings): Cheap = `claude-haiku-5-5` at effort `low`, Balanced =
`claude-sonnet-5-5` at `medium`, Max = `claude-opus-5-5` at `high`. The default tier is
Cheap. Prices live in config too and are used only for the estimate shown in the UI.

## D6. Claude request shape

- Streaming via `client.messages.stream` from the official `anthropic` SDK.
- Prompt caching: the system prompt block carries `cache_control`, and top-level
  automatic caching also caches the conversation prefix. The system prompt is byte-stable
  (no timestamps). Note: prefixes under the model's cache minimum (512 tokens on current
  models) silently don't cache. That is fine while the prompt is short.
- Assistant turns are replayed with their full content blocks (including thinking
  blocks), stored as `provider_state`, so the history stays append-only and valid.
- Balanced and Max tiers enable the API's server-side refusal fallback
  (`server-side-fallback-2026-07-01`, `fallbacks: "default"`). Haiku has no server-side
  fallback, so Cheap leaves it off. You can turn it off per tier with
  `refusal_fallback: false`.
- Errors map to typed codes (`no_api_key`, `auth`, `offline`, `rate_limited`,
  `overloaded`, `bad_request`, `provider_error`). The UI shows `offline` as a clear state.

## D7. One local adapter for all free local servers

Ollama, LM Studio, llama.cpp server, and vLLM all expose the OpenAI-compatible
`/v1/chat/completions` streaming endpoint. One `httpx` adapter covers all four. It is
optional and never the default.

## D8. Contracts mirrored by hand in TypeScript

`frontend/src/contracts.ts` mirrors `backend/contracts/ws.py`. A backend test fails if
the set of message `type` literals differs. Code generation can replace this when the
protocol grows.

## D9. Settings stored in SQLite as one validated JSON document

There is one pydantic `Settings` schema with a `schema_version`. It is stored in the
`settings` table and validated on every load and save. Unknown providers or tiers are
rejected with HTTP 422.

## D10. Audit log is append-only at the database level

SQLite triggers abort any `UPDATE` or `DELETE` on `audit_log`. Details are redacted
before insert. WAL mode keeps the log and other data safe if the app crashes.

## D11. Security policy file at repo root

The architecture lists `docs/SECURITY.md`. We use a root `SECURITY.md` instead, because
that is where GitHub looks for a vulnerability-reporting policy. The security model is
described in the README.

## D12. Risk is assessed per call, not per tool

`Tool.assess(args)` returns the real risk for one call, plus a one-line summary and an
optional blocking error. Example: `write_text_file` is `write` when it creates a file and
`destructive` when it overwrites one. The policy gate uses the assessed risk. The worker
assesses again at run time, so state that changed after approval (say, a file that now
exists) can't slip through.

## D13. Policy order

1. Any required capability set to `deny` → denied, without asking.
2. Risk `destructive`, `external_send`, or `purchase` → always ask. "Allow for this
   chat" is never offered.
3. The chat is **tainted** and the call isn't a read → ask, and no remembering.
4. Any capability set to `ask` → ask, unless the user chose "allow for this chat" for
   this tool earlier in the chat.
5. Otherwise allow.

Defaults: `fs.read` is allow. Every other capability is ask.

## D14. Prompt-injection defense by taint, in code

Tool results carry a `trust` tag. File contents, directory listings, and command output
are `untrusted`. Once an untrusted result enters a chat, the chat is marked tainted (this
is stored, because the content stays in history). From then on every non-read action in
that chat needs a fresh approval, and the approval card says why. Untrusted output also
reaches the model wrapped in `<untrusted_content>` with a short note. That wrapper helps,
but the real defense is the approval gate.

## D15. OS tools run in a separate worker process

`workers/os_tools` runs as `python -m workers.os_tools` and speaks line-delimited JSON
over stdin/stdout (`contracts/worker.py`). Core never imports worker code. Core starts the
process from a blocking reader thread, which works with any event loop, including the
Windows selector loop. The worker's environment has API keys, tokens, and anything
secret-looking stripped out. If the worker crashes, pending calls get an error result and
the worker restarts on the next call (at most 3 restarts a minute). Cancelling a turn
sends `cancel`, and the worker kills the running command.

## D16. Shell sandbox

`run_command` takes an argv list and runs it with no shell, so there are no pipes,
globbing, or `;`. The program has to be a bare name found on PATH, and it has to be on
the user's allowlist. A hard blocklist always wins over the allowlist: privilege tools,
deletion, disk and power tools, permission changes, process killing, shells and
interpreters, exec wrappers (`env`, `xargs`, `find`, …), and network clients. Any argument
that looks like a path must pass the same folder confinement as the file tools. Other
limits: a minimal environment, a time limit, capped output, and the whole process group
killed on timeout or cancel. **Known limit:** this is policy confinement, not an OS
sandbox. An allowlisted program can still do anything it is able to do inside the
allowed folders. That's why `shell.run` defaults to ask.

## D17. Folder confinement

Paths are resolved, which follows symlinks, so a link can't escape. Every path must sit
inside an allowed root (default `~`). Protected locations are always refused: SSH, GPG,
cloud credentials, keychains, and the app's own data folder (auth token, database). The
app data folder is added in code, so it can't be removed in Settings. On macOS and
Windows, comparisons ignore case.

## D18. Agent loop limits

Each turn has these limits, all configurable in settings: `max_steps` (8 model calls),
`max_turn_tokens` (200k), and `max_identical_calls` (2 per turn, to catch loops). A tool
call whose reply was cut off (`max_tokens`) or declined is never run. Every tool call is
stored together with its result. A call that never ran gets a "Not run: …" result, so
history stays valid for the API after cancels, denials, and crashes.

## D19. Local models get text only

The OpenAI-compatible adapter ignores tool definitions for now, because function-calling
support varies between local servers. Tool results in history reach the local model as
text.

## D20. Kill switch and pause

`control.stop_all` (header button, or Ctrl+Shift+K while the UI has focus) denies every
pending approval and cancels every running turn on every connection. This includes
running worker commands. `control.pause` does the same and also rejects new messages
until you resume. The pause state is sent to every open tab. A global OS hotkey comes
with the voice milestone.

## D21. Instant-command router runs before any model call

`core/router` matches a cleaned message against anchored rules: arithmetic, time, system
info, web search, open URL, open a known folder, media and volume, and open app. The
first match wins. Only a full match counts, and any compound request falls through to
the model (`and`, `then`, commas, or, for searches, "and book/send/…"). So do rules whose
target can't be verified: `open_app` must find exactly one installed app, and folders
must exist. If the OS tools are unavailable, everything goes to the model. A
`chat.done` with `route: "instant"` means zero LLM calls. Instant turns are stored in
history as plain text, so later model turns have the context.

## D22. Instant actions use the normal tool path

Instant commands run through the same `ToolExecutor`, so the policy gate, audit log,
and kill switch all apply. There's one difference: a command the user typed themselves
is a direct request, so an "ask" capability is allowed without a prompt
(`router.direct_without_asking`, on by default). The model can't produce such a call,
so taint doesn't matter for it. "Never" still denies, and destructive, sending, and
purchase actions still always ask.

## D23. Free, keyless web search

Search opens the user's browser at a URL built from a template. The default is
DuckDuckGo. Site templates cover YouTube, Wikipedia, GitHub, OpenStreetMap, and Reddit.
Templates must be http(s) and contain `{query}`, so a self-hosted SearXNG URL works too.
There's no search API and no API key.

## D24. Per-OS app launching and media control

- **Apps.** Linux reads `.desktop` entries from the XDG, Flatpak, and Snap folders and
  launches them with `gio launch` (or `gtk-launch`). macOS scans `.app` bundles and uses
  `open -a`. Windows uses Start Menu shortcuts and `os.startfile`. Name matching is
  fuzzy. A tie is reported as ambiguous rather than guessed, and shorter names win when
  the match is otherwise equal.
- **Media.** Windows sends virtual media keys. Linux uses `playerctl` for playback and
  `wpctl` or `pactl` for volume. macOS uses fixed AppleScript for system volume and for
  Spotify and Music playback. There's no generic media key from the macOS command line,
  so that's a known limit.

## D25. Latency budgets in tests

`core/latency.py` sets two budgets. Router matching must stay under 5 ms at p95. An
instant command must finish its action within 1000 ms end to end over the WebSocket.
Tests enforce both. Every `chat.done` carries `timings` (`route_ms`, `action_ms`), and
the daemon log prints them per turn.

## D26. Hands on Linux go through AT-SPI over raw D-Bus

The first Hands port is Linux (GNOME 46 on Wayland). On Wayland, apps can't move other
windows, and X11 tools like `xdotool` and `wmctrl` only reach legacy X11 apps. So the
backend uses **AT-SPI**, the accessibility bus that screen readers use. It works the same
on Wayland and X11, and it also works on covered windows. We call its D-Bus interfaces
directly with `jeepney` (pure Python, MIT, already a dependency through `keyring`), so no
compiled GObject bindings are needed in the venv. Elements are pressed with
`Action.DoAction` and text is set with `EditableText`, so the pointer never moves.

## D27. Every action is verified by before/after snapshots

Before an action, the backend records the names, text, and key states of the window's
controls. Afterwards it polls for up to 0.8 s and reports human-readable changes, such as
"label now 'Count: 1' (was 'Count: 0')". Text entry is read back and compared exactly.
`verified: false` means the action ran but nothing visible changed, and the tool output
says so plainly.

## D28. Keys only reach a window that is confirmed active

Key combos and scrolling use a Linux uinput virtual device (pure-Python ioctls, no
`ydotoold`). uinput can't target a specific window. So `press_keys` and `scroll` first
check that AT-SPI reports the target window `ACTIVE`, and refuse otherwise. Keystrokes can
never land in the wrong app. Verified live on the user's GNOME Wayland session.

## D29. Hands safety rules

- **Password fields** (`password text` role): their text is never read or returned, and
  `type_text` refuses them during assessment, before anything is asked.
- **App blocklist** (`tools.blocked_apps`, default: common password managers and key
  stores): those apps' windows are hidden from listings, and every action on them is
  refused.
- **Risk from labels.** Clicking a control labelled Buy/Pay/Checkout is `purchase`,
  Send/Post/Submit/Share is `external_send`, and Delete/Remove/Erase/Reset is
  `destructive`. Menu paths are classified the same way. Labels are untrusted, so this
  only ever raises risk.
- **Destructive key combos.** `alt+f4`, `ctrl+q`, `ctrl+w`, and `shift+delete` are
  `destructive`.
- **Screen content is untrusted.** Window lists, control lists, and action reports are
  untrusted output, so the chat becomes tainted and every later action needs approval.
- `screen.read` asks by default. `input.control` asks, except for direct instant commands.

## D30. Short ids for the model

The worker maps long AT-SPI handles to `w1` and `e7`, with labels like
"push button 'Increment' in App — Title". This cuts tokens, and it gives approval cards
readable summaries.

## D31. Not done on Wayland yet, by design

`move_resize` and coordinate `click` return clear errors instead of pretending. Moving
windows isn't possible from an app on GNOME Wayland. Coordinate clicks need screen
capture to be useful, so they come with milestone 5, using the portal-based input path.
Porting Hands to macOS (AX) and Windows (UI Automation) uses the same contract test suite.

## D32. Testing Hands without touching the user's desktop

`tests/a11y_session.py` starts a private D-Bus session, a private runtime dir (so the
user's real accessibility bus socket is never touched), Xvfb, and a GTK fixture app.
The fake backend and the real AT-SPI backend must pass the same contract tests. The
fixture runs on the system `python3` with `python3-gi` (test-only; not installed into
the venv and not shipped). CI installs these with apt.

## D33. Screen capture: accessibility first, images second, never stored

`capture_screen` lives in the hands worker, so it shares window ids and the blocklist. Its
description tells the model to use `find_elements` first, because control text is cheaper
and more reliable than an image. When an image is needed:
- **Size.** It's downscaled to `tools.capture_max_edge` (default 1280 px, about 1.4k
  tokens) and sent as JPEG.
- **Thumbnail.** A separate 480 px preview goes only to the local UI, as a `data:` URL.
- **Current turn only.** Images go to Claude in the turn they were taken. When the turn
  is saved, each image is replaced by a short note. So screenshots never reach the
  database, and old images are never re-sent or paid for again.
- **Portal files.** When the portal writes a file, we read it and delete it at once.

## D34. Capture backends

- **Wayland:** the xdg-desktop-portal Screenshot API, the only sanctioned path. GNOME's
  private screenshot API refuses third-party callers. On GNOME 46, an app outside a
  Flatpak sandbox gets no silent full-screen screenshots until the user grants a portal
  permission. The app explains that and shows the exact command, and never changes the
  setting itself.
- **"Pick region"** (`select`) opens GNOME's own screenshot picker. The user decides
  exactly what to share, so it always works.
- **Wayland window capture** is refused, because Wayland doesn't expose window positions.
  "Pick region" covers that case.
- **X11, macOS, Windows:** Pillow `ImageGrab` (MIT-CMU). Window capture crops to the
  AT-SPI window bounds.

## D35. Blocklisted apps stop screenshots

If an app on `tools.blocked_apps` has a window showing (visible and not minimized),
screen and window captures are refused at assessment time, before any pixel is read.
The OS picker (`select`) is allowed, because the user chooses the region themselves.

## D36. "Where do I click?" highlight

- `highlight_area` takes a box in the pixels of a capture the model saw. It is risk
  `read` with no capability, because it only draws.
- The worker keeps each capture's geometry (never the image) and maps the box to
  desktop coordinates.
- **On screen:** a small helper process (`overlay_gtk.py`, on the system's GTK through
  XWayland) draws four thin bars and a label for 6 seconds. The middle stays empty, so
  the user's click goes straight through to the target.
- **In the app:** the same box is always drawn on the local preview.
- The assistant points; the user clicks. Coordinate clicking stays out on purpose until
  it can be verified through the RemoteDesktop portal.
- The overlay is optional. Without system GTK (python3-gi), only the in-app highlight
  shows. It isn't bundled.

## D37. User-attached screenshots

Two composer buttons control attaching:
- **Show screen** attaches the whole screen.
- **Pick region** opens the OS picker.

The capture runs through the normal executor as a direct request. That means it is
audited, still honors "Never" and the blocklist, and is shown as a tool card with its
preview. The image travels with the user's message, which skips the instant router.

## D38. The skill pipeline position

The request ladder is now: learned skill → rule router → model. A skill is only tried
when the user typed the request themselves: no "Ask Claude instead" flag and no attached
screenshot. Only `active` and enabled skills replay.

## D39. What gets learned (conservative on purpose)

After a model turn ends with `end_turn`, the recorded tool trace becomes a skill
candidate. It qualifies only when all of these hold:
- every call succeeded, and every action that reports verification was verified;
- at least one call changed something; locate-only calls (`list_windows`,
  `find_elements`) are dropped from the skill;
- nothing read outside content that could have shaped later arguments. This is checked
  by tool name, and by any `read`-risk tool that returned untrusted output, so plugin
  tools are covered too;
- the chat wasn't already tainted before the turn;
- no argument looks like a secret, and the request doesn't mention passwords, logins,
  or payment details;
- free-text arguments (`content`, `text`) come almost entirely from the user's words. If
  the model wrote the content itself, a replay would only repeat it.

## D40. Generalization

The learner looks for word spans of the request (the longest first, skipping spans made
only of stopwords) inside the argument values:
- A match on whole-token boundaries becomes a slot. `volume` must not match inside
  `volume_up`.
- Inside a URL's path or query, a slot is always `{name|url}`, so it's URL-encoded on
  replay. A slot covering the whole host (as in "open github.com") stays raw. A span
  that is only part of a domain (`wikipedia` in `en.wikipedia.org`) stays constant.
- Numbers become number slots.
- Hands ids (`w1`, `e7`) are swapped for the locator each action reports
  (app / role / name), so the skill works in later sessions.
- The trigger is the cleaned request with its slot spans replaced.

## D41. Merging, promotion, demotion

- **Merging.** Runs with the same step-shape signature merge into one skill, and new
  phrasings are added as triggers (up to 8).
- **Promotion.** A skill becomes `active` after `skills.promote_after` verified successes
  (default 2).
- **Demotion.** `skills.demote_after_failures` consecutive replay failures (default 2)
  set it to `demoted`. A demoted skill stops replaying until the user re-enables it, or
  until the model succeeds with the same shape again.

## D42. Replay safety

- Every replayed step goes through the normal `ToolExecutor`, so the policy gate, audit
  log, and kill switch all apply.
- Replay counts as a direct request (`skills.replay_without_asking`, default on). So
  "Never" still blocks, and destructive, sending, and purchase steps always ask again.
- After each step the recorded checks must hold again. `verified` means the hands
  backend observed a change.
- If any step fails, the replay stops and the reply says honestly where it stopped. It
  doesn't silently fall back to the model, which could repeat earlier steps.

## D43. Matching

- Matching is embedding-free. Each trigger becomes an anchored, case-insensitive pattern
  with lazy slot groups.
- Number slots only match numbers.
- Slot values are trimmed of quotes and must be 1–200 characters on one line.
- When several skills match, the trigger with the most fixed text wins. A tie between
  different skills falls through to the model.

## D44. Skill evals

`tests/evals/test_skill_evals.py` runs scripted scenarios end to end over the WebSocket.
Real tasks: web search on a site, opening an app then a page, writing a note, and
pressing a button by locator. Traps: reading content first, passwords, and generated
content. It reports learn rate, replay reliability with new parameters (including
URL-encoding), whether replay made zero model calls, and the false-learn rate. CI
requires 100% / 100% / 0%. Run it with `pytest tests/evals -s`.

## D45. Speech-to-text: Vosk, not sherpa-onnx (license)

sherpa-onnx was the first choice: streaming Zipformer, Apache-2.0 on PyPI. But its native
libraries statically include **espeak-ng (GPL-3)**, even when only speech-to-text is
used. I found 109 `espeak` symbols in `libsherpa-onnx-c-api.so`. Bundling that would put
copyleft code inside our MIT app, so it was removed.

**Vosk** replaces it:
- **License:** the engine (Kaldi inside) and its English models are Apache-2.0. The
  native library has no espeak and no GPL code.
- **Small model:** release → final 10–20 ms on this machine.
- **lgraph model:** release → final 120–140 ms, and more accurate.

Both are well under the 300 ms target, so faster-whisper's chunked approach wasn't
needed.

## D46. Text-to-speech: the computer's own local voices for now

Every good open neural TTS (Kokoro, Piper, Matcha) relies on espeak-ng (GPL-3) to turn
text into phonemes, and Piper voices each carry their own dataset license (Lessac's is
restrictive). So spoken replies use the browser's Web Speech API, **filtered to
`localService` voices**. Those are on-device; network voices that send text to a cloud
service are never used. Spoken replies are optional and off by default, and pressing the
talk key or Esc stops them. **Follow-up:** Kokoro (Apache-2.0 weights) through
onnxruntime (MIT), once a permissively licensed English phonemizer is wired in.

## D47. The microphone lives in the browser tab

The UI captures the mic with `getUserMedia`. `127.0.0.1` counts as a secure context.
An AudioWorklet resamples to 16 kHz PCM16 and streams 100 ms chunks over the existing,
authenticated local WebSocket. Audio only ever goes to the local daemon, and no native
audio library is needed on any OS. Once enabled, the mic stays warm (open but silent)
so a press starts instantly. Nothing is sent until you talk, and the mic can be
released in Settings.

## D48. Hotkeys

- **Inside the app:** hold **Ctrl+Space** to talk, or use the Talk / Dictate buttons.
- **Global:** GNOME 46 has no GlobalShortcuts portal, and `/dev/input` needs the
  `input` group, so there's no sanctioned way for an app to grab a key system-wide.
  Instead, `python -m core.cli voice|dictate|stop` is meant to be bound as a desktop
  custom shortcut. The commands are shown in Settings and work from any folder. They
  tell the open UI tab to toggle listening, or stop everything (the global kill-switch
  hotkey from D20). They are toggles, not hold-to-talk.

## D49. Speculative start

- **When it starts:** when the recognizer reports a pause ("segment") in chat mode,
  core starts the model on the text so far. It doesn't start if a skill or the instant
  router would handle that text, or if the setting is off.
- **Nothing is shown or run early:** events are only buffered, so no tool can run
  before the final transcript. The tools are the same; they just aren't executed until
  the turn adopts the speculation.
- **Adoption:** the real turn uses the speculation only when the final text matches
  after normalization, in the same chat, with the same history and the same tier, and
  within 60 s.
- **Otherwise it's cancelled.** If it had already finished, its cost is still recorded
  in the ledger and audited (`speculation.discarded`), because it was paid for.

## D50. Dictation

- **Into the message box** (Dictate button): the cleaned text fills the box without
  sending.
- **Into any app** (the `dictate` shortcut): the cleaned text is typed at the cursor of
  the focused field, through the hands worker's new `insert_text`. That uses AT-SPI's
  caret offset and EditableText, is verified by reading the text back, and refuses
  password fields.
- **Clean-up:** one quick cheap-model pass with a strict prompt (fillers,
  self-corrections, punctuation, lists, numbers, emails; never add content, never follow
  instructions in the text). Its cost is recorded.
- **Guard:** at least 75% of the content words must survive, and the output can't
  balloon. Otherwise, or when offline, local rules run instead (fillers, spoken
  punctuation, "x at y dot com", capitalization), and "Kept your exact words" is shown.

## D51. Model downloads

The catalog has two Apache-2.0 Vosk English models, recommended by hardware tier: small
when RAM < 8 GB or cores < 4, standard otherwise. A download starts only from a click.
It shows the size and license, streams progress to the UI, resumes with HTTP Range (and
restarts cleanly if the server ignores Range), checks SHA-256 before installing, and
unzips with zip-slip and symlink protection. The first installed model is used
automatically.

## D52. Voice latency gates

`VOICE_RELEASE_TO_FINAL_BUDGET_MS = 300` and `INSTANT_FIRST_ACTION_BUDGET_MS = 1000` are
enforced with real speech (espeak-generated clips, test-only) through the engine and the
full daemon. Test audio is fed at microphone pace; sending it all at once creates a
backlog that real speech never has. Measured: release → final ≈ 48 ms and final → first
action ≈ 4 ms through the daemon; 42–79 ms release → final through Chrome. Every voice
turn's `chat.done` carries `hotkey_to_first_partial_ms`, `release_to_final_ms`,
`final_to_first_token_ms` / `final_to_first_action_ms` and `speculation_used`, and the
daemon logs them. CI caches the small model (checksum-verified) and installs espeak-ng
to run these.

## D53. Background tasks are ordinary turns, detached

A background task is a normal chat turn (same agent loop, policy gate, approvals, audit)
run by `BackgroundManager` with `background=True`. It has its own session, so it never
interleaves with the chat you're in. Its events are broadcast to every open tab, so an
approval can be answered from any of them. The tab's chat reducer ignores turns it
didn't start, and the Tasks panel shows them. A `task.update` (status, step, current
tool, last 300 characters of reply, tokens, cost) is sent on every status change and at
most every 0.5 s otherwise; only status changes are written to the database. Concurrency
is capped (`background.max_concurrent`, default 2), and extra tasks queue.

## D54. Background tasks never take over the screen

`policy.decide(background=True)` denies the focus-stealing tools (`focus_window`,
`press_keys`, `scroll`, `open_app`, `open_url`, `open_path`, `insert_text`) when the
model calls them, with a reason the model can relay. This is a rule, not a prompt
instruction. A command the user typed (an instant route) is still allowed, because the
user asked for it directly. Reading the screen (screenshots, element lists) is allowed;
the screen.read permission still applies.

## D55. Per-task budget

Background turns replace the interactive limits with `background.max_steps` (25) and
`background.token_budget` (100k input + output tokens across all steps). Hitting either
ends the turn with `stop_reason` `step_limit` or `budget`, exactly like the interactive
limits. Routine runs and watcher actions are background tasks, so the same budget
applies.

## D56. Crash recovery needs the user

At startup, any task stored as queued, running or waiting is marked `interrupted`, and
one notification is sent. The UI also derives that notice from the task list on
reconnect, because the startup broadcast happens before any tab is connected. Nothing
reruns on its own. **Resume** starts a new turn in the same session ("Continue this
task where it stopped; some steps may already be done"), because tool side effects
can't be rolled back. **Discard** marks it cancelled.

## D57. Scheduler inside the daemon; missed runs are reported

Routines run from a 5-second loop in the daemon; no OS scheduler (cron, Task Scheduler,
launchd) is installed. Schedules (`once`, `daily`, `weekdays`, `weekly`, `interval` of
at least 5 minutes) are evaluated in local time by pure functions in `core/schedule.py`.
A routine more than 15 minutes overdue at startup (the daemon was off) is reported as
"Missed" and rescheduled, never run late: a "do X at 8" that runs at 14:00 can be wrong.
Re-enabling a routine schedules from now, so there's never a backlog. **Pause** holds the
loop; **Stop all** cancels running background tasks.

## D58. Watchers are zero-LLM and quiet

Watchers check URLs (up/down/contains/changes), files (exists/gone/changes) and window
titles (exists/gone) with plain code, never with the model. A URL check is one GET with a
10 s timeout, reading at most 1 MB. Only a hash is kept for "changes", never the page
or the window titles. "changes" takes a baseline on the first look and fires only after
that. A watcher fires once by default (`triggered`); **Watch again** resets it and takes
a new baseline. Polls don't write audit entries (one every 15–60 s would bury the log);
creation, changes, deletion and every notification are audited. If the capability a
watcher needs (net.fetch, fs.read, screen.read) is later set to Never, the watcher
pauses itself instead of polling.

## D59. Creating automations needs the new `schedule` capability

`create_routine` and `create_watcher` are WRITE tools with `Capability.SCHEDULE`, which
is "ask" by default. A web page or file the model read can't quietly set up something
that runs later. When the model proposes one, the approval card shows the parsed
schedule ("Every weekday at 09:00, run: …"). Typed phrases ("every weekday at 8am, …",
"remind me …", "tell me when …") match instant rules and are direct requests, so they
save without a prompt, like other instant commands. Vague phrases ("every blursday",
"tell me when you're done") fall through to the model.

## D60. Internal tools

`desktop_notify` (Linux `notify-send`, macOS `osascript` with the text passed as
arguments, never through a shell or script string) is internal: it is excluded from the
model's tool list, and calls to it from the model are treated as unknown tools. The
notifier calls it directly. Desktop notifications can be turned off with
`background.desktop_notifications`. `file_info` (exists, size, mtime inside the allowed
folders) was added for file watchers and is an ordinary READ tool.

## D61. Memory: plain notes, given whole when they fit

Memories are short notes (500 characters max) in SQLite. No embeddings: the brief's
"cheap by construction" rule and a local-only stack make keyword matching enough for a
personal notebook. Every model request gets the notes as a **second system block after
the cached system prompt** (`ModelRequest.context`). The system prompt stays byte-stable
for prompt caching, and the notes are labelled as facts about the user, not
instructions. When all notes fit the budget (`memory.max_prompt_chars`, 3,000), all of
them go in, in saved order, so the prefix changes only when memory changes and the
conversation cache keeps hitting. Past the budget, the notes sharing the most words with
the request win (ties go to the newest), still in saved order.

## D62. Memory writes need the user (memory poisoning)

A note saved from a web page could steer every later chat, so `remember` and `forget`
are WRITE tools with a new `memory` capability that asks by default. "Remember that…" /
"forget about…" / "what do you remember" typed by the user are instant rules (direct
requests: no prompt, no model call). Whether a note came from the user or from an
approved model call is recorded (`source`) and shown. "Remember to…" is left alone: it
means a reminder, not a fact.

## D63. Spend caps at one choke point

`CappedProvider` wraps whichever provider is active, and `Runtime.provider()` returns
only the wrapper. Chat, background tasks, routines, speculation and dictation clean-up
therefore all pass the same check, and a future call site can't forget it. At a cap,
`stream()` yields `ModelError(code="spend_cap")` with a plain explanation; dictation
falls back to local clean-up as it does offline. The check reads the cost ledger, which
is local and indexed, on each call. Defaults are $2/day and $20/month: safe for a new
user, editable or removable (an empty value means no cap). A cap of $0 means "never call
Claude". The meter warns at 80% (`spending.warn_at`). Costs are estimates from the price
table; the Anthropic bill is authoritative.

## D64. Data control

- **Export:** one JSON file with every table (JSON columns decoded). There are no
  secrets in the database. The API key is in the keychain and the auth token is a
  separate file; neither is exported.
- **Delete all data:** needs the literal `"DELETE"` in the request body. The daemon stops
  everything, exits, and after closing the database deletes **only the files it
  creates**: database plus WAL/SHM, auth token, models folder. Then the keychain entry
  and login item, and the data folder if it's empty. It never removes a whole folder,
  because `ASSISTANT_DATA_DIR` may point somewhere shared.
- **Uninstall:** `python -m core.cli uninstall` does the same. If the app is running, it
  asks the daemon to delete and waits for the token file to disappear (on Windows, open
  files can't be deleted). The bundle's uninstall script then removes the program.

## D65. Start at login is the OS entry itself

It is opt-in and off by default. The OS entry is the only state: an XDG autostart
`.desktop` file (Linux), a LaunchAgent plist (macOS), or an HKCU `Run` value with
`pythonw.exe` (Windows, no console). All are per-user and need no admin rights. Nothing
in settings can drift out of sync with it.

## D66. Packaging: wheels + offline bundles + keyless signatures

- **No frozen executables:** PyInstaller is GPL-with-exception, and unsigned frozen apps
  trigger Gatekeeper/SmartScreen.
- **What ships:** a pure-Python wheel with the built web UI inside (`core/webui`), plus,
  per OS, a zip with every dependency wheel for that OS at the pinned versions and
  install/uninstall scripts. Install needs only Python 3.12+: no internet, no admin.
- **Signing:** releases are checksummed (`SHA256SUMS`) and the checksum file is signed
  keylessly with sigstore cosign in CI. That is free, there's no key management, and the
  signature is bound to the repository's release workflow.
- **Release gate:** each OS job installs its bundle, starts it, checks `/api/health` and
  the UI, then uninstalls it, before anything is published as a draft release.
- **Naming:** the distribution, console script and app id are all `local-assistant`
  (`APP_ID`). A test keeps `pyproject.toml` and `contracts/app.py` in sync.
- **Not done:** Homebrew, winget and AUR need accounts or maintained external repos.
  They're possible later; pipx with the wheel covers "package manager" installs today.

## D67. Plugins are processes with a manifest; channels first

- **Shape:** a plugin is a folder with `plugin.json` and a package, run as
  `python -m <entrypoint>`.
- **Isolation:** it gets the workers' scrubbed environment and only its own declared
  secrets, sent over stdin from the OS keychain. Its imports are limited to `contracts`
  (enforced by the import-boundary test).
- **Discovery:** first-party plugins live in `backend/plugins/` and user plugins in
  `<data>/plugins/`. A user folder can't take a first-party name. Name, folder,
  entrypoint package and contract major version must all match.
- **Off by default:** enabling one shows what it needs, and warns when it's
  third-party.
- **Honest limit:** a separate process is crash isolation, not a security sandbox; the
  docs and UI say so.
- **Scope:** `provides` accepts only `"channel"` for now. Tool and model-provider plugins
  stay reserved instead of half-built.

## D68. Chat-app requests are "remote" and always ask before changing anything

A chat account can be lost, shared, or hijacked, and its messages arrive from the
internet.

- **Remote rule:** chat-app conversations get their own sessions, marked `remote` in
  `ChatPermissions`. The policy gate then asks for every non-read action, with no "remember"
  and no "direct" pass for typed commands. This sits after deny and the always-ask risks,
  and before settings "Allow" and session grants.
- **Reads** behave as usual, so questions and lookups stay quick.
- **Approvals:** come back as buttons, and only the asked account's press counts.
- **Same as local turns:** pause, Stop all, spend caps and the audit log all apply. The
  audit records the channel, user id and message length, not the text (the text is in
  the chat history like any chat).

## D69. Pairing, not passwords

- **Pairing:** the owner presses **Pair an account** and sends `/pair <code>` from the
  account. The code is 6 digits, single use, valid 10 minutes, and dropped after 5 wrong
  tries; it's compared in constant time.
- **Allowlist:** paired accounts are stored in settings per channel, and removing one
  cancels its running turn.
- **Unpaired senders:** get at most one short reply an hour. Everyone is rate limited
  (10 messages a minute by default).
- **One turn per conversation;** a second message gets "still working, /stop to cancel".

## D70. Telegram via long polling, plain text

- **Long polling:** `getUpdates` needs no public URL, webhook or open port, matching
  the no-servers rule.
- **What it takes:** private chats only (groups are ignored) and plain text only (no
  parse modes, link previews off).
- **Approvals:** sent as an inline keyboard. `callback_data` is
  `ok:<approval_id>` / `no:<approval_id>`, and the buttons are removed by editing the
  message once resolved.
- **Errors:** a bad token stops polling with a clear status instead of retrying
  forever; network errors back off up to 60 s.
- **Token safety:** the token is in every request URL, so the plugin never logs HTTP
  requests, reports errors by type only, and core adds the token to its log redactor.
  The browser test checks that the token appears neither in the daemon log nor in the
  activity log.
- **Testing:** a fake Bot API server (`tests/fake_telegram.py`) drives unit,
  end-to-end and browser tests without a real bot.

## D71. Two UI surfaces, one session

The chat window stays the single owner of the microphone and the chat. The overlay
neither records audio nor starts turns; it mirrors them.

- **Why the mic stays in the chat window:** the browser already holds a warm,
  permission-granted mic stream and has an accessible UI. Capturing audio in the overlay
  would need a native audio dependency (PortAudio) and a second permission prompt.
- **One session:** a voice turn is a normal `chat.send` from the chat window's current
  session, so history, memory, skills, approvals and spend caps behave exactly as for
  typed turns.
- **How the overlay follows along:** every message the chat window is sent also passes
  through `OverlayController.observe`. It reacts only to voice streams and to request
  ids marked as voice turns (`chat.send` with `voice` set). Typed turns and background
  tasks never open it.
- **Trade-off:** voice needs the chat window open somewhere, even in a background tab.
  If the hotkey fires with no window connected, the overlay says "Open the chat window"
  instead of silently doing nothing.

## D72. The overlay is Tk in its own process

- **Toolkit:** Tk ships with Python on Windows and macOS (Linux: `python3-tk`). It's
  lightweight, permissively licensed and cross-platform; Qt, GTK and web views were
  ruled out as heavy, copyleft or platform-specific.
- **Isolation:** it runs as a worker-style process (`python -m workers.overlay`,
  contracts-only imports, scrubbed environment) and talks to core over private pipes, so
  it needs no auth token or network. Missing Tk or display → `available: false`, shown
  in Settings → Voice; it restarts at most 3 times an hour.
- **No focus stealing** (per OS):
  - X11: an override-redirect window, which the window manager never focuses.
  - Windows: `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW` and `SW_SHOWNOACTIVATE`.
  - macOS: Tk's `help` window style with `noActivates`.
- **Keyboard:**
  - The overlay takes focus only when clicked: `XSetInputFocus` via ctypes on X11
    (Tk's focus -force doesn't move X focus for override-redirect windows; found by the
    Xvfb test), `SetForegroundWindow` on Windows.
  - Then Tab/Enter/Space work, `Y` approves, `N` denies, and Escape stops or closes.
  - Without focusing it at all: the `approve` / `deny` / `stop` desktop shortcuts
    (`core.cli`), and everything is also in the fully accessible chat window.
- **Placement:** top-centre of the display under the pointer.
  - Monitor lists come from Win32 `EnumDisplayMonitors` (work area), CoreGraphics
    `CGDisplayBounds`, or `xrandr --listmonitors`, all via ctypes or the command, with
    no new dependencies.
  - It's placed once when shown and doesn't jump while visible.
  - With no monitor list it falls back to the virtual screen.
- **Auto-hide:** 4 s after done, 6 s after an error, 1.5 s after stop. A pending
  approval keeps it open, and the approval is never auto-answered (it times out like any
  approval).
- **Limits:**
  - Tk widgets aren't exposed to screen readers. That's why everything the overlay
    shows is also in the chat window's live regions.
  - On Wayland, Tk runs through XWayland: the pointer position is only known over X11
    windows and the compositor may ignore always-on-top, so multi-display placement
    there needs manual testing.

## D73. Spoken replies for typed turns

`voice.speak_chat_replies` (off by default) reads replies to typed messages aloud, and
every reply has **Read aloud**. Only the computer's built-in voices are used
(`localService`). When a computer has none, Read aloud says so. Turning a setting on
never reads out replies already on screen, and loaded history is never read
automatically.

## D74. Cheap first, escalate once

The brief asks for the Haiku-class tier by default, moving up only on failure or for
multi-step work; this was missing until now.

- **Rule:** a turn that starts on **Cheap** moves to **Balanced** for its remaining
  steps when a tool step fails, or once it reaches 3 tool rounds (`agent.escalate`,
  `agent.escalate_after_steps`). It happens at most once per turn.
- **When it doesn't apply:** an explicitly chosen tier (Balanced/Max) is never changed.
- **Visibility:** `chat.done` carries `escalated` (the reason), which is shown in the
  reply's details and recorded in the audit log.
- **Step limit:** the default rose from 8 to 15, because real web tasks need more steps;
  the 200k-token turn cap and loop detection still bound cost.

## D75. Browser: a worker process, structure not pixels

- **Process:** Playwright runs in its own worker (`workers/browser`, contracts-only
  imports, crash-isolated) with the same tool protocol as the other workers, and the
  browser is started on first use.
- **What the model sees:** each snapshot tags visible interactive elements with short
  refs (`e12`) and returns them plus whitespace-collapsed visible text, capped at 4,000
  characters for the model. Raw HTML and screenshots are never sent, which keeps tokens
  low. Password and card values are never read out of the page.
- **Trust:** every page result is untrusted, so it taints the chat as any outside
  content does.

## D76. Browser risk levels and the final click

- **Risk per mode:** in the isolated profile, browsing only affects that profile, so
  clicking, typing and selecting are READ under the new `browser` permission (asks
  once, can be remembered for a chat). In "own browser" mode the same actions are WRITE,
  so with page content in the chat they ask every time.
- **Sending details:** submitting a form that holds email, phone, name or address fields
  is EXTERNAL_SEND, which always asks.
- **Commitments** (pay, book, place order, confirm, subscribe, delete account, or any
  submit of a form with card fields) are recognised by pure rules (`safety.commitment`):
  - `browser_click` refuses them both at assessment and again at run time;
  - `browser_finish` is PURCHASE: it always asks, can't be remembered, and shows what /
    price / dates / passengers / refund terms;
  - on approval it **hands over** the browser with the button highlighted, and the
    worker never clicks it. This holds even if Purchases is set to Allow (the rule is in
    code, not the prompt).
- **Sensitive fields** (password, `cc-*`/`one-time-code` autocomplete, card/CVV/IBAN/SSN/
  passport/OTP names) are refused by `browser_type`.
- **Challenges:** a CAPTCHA frame, an "are you human" page or a two-factor prompt pauses
  the task with a hand-over. Nothing tries to solve or evade them.

## D77. Profiles: isolated by default, own browser by opt-in

- **Isolated (default):** a persistent profile in `<data>/browser` (deleted by "Delete
  all data"), headless by default so it never takes the screen.
- **Handing over:** relaunches that profile in a visible window at the same page. Cookies
  and logins carry over; unsaved form fields may not, which is the trade-off for not
  stealing focus. "Show the browser window" keeps it visible throughout instead.
- **If no window can be shown:** it falls back to headless at the same page and says so.
- **Own browser:** connects over CDP to the user's Chrome started with
  `--remote-debugging-port`, and works in a **new tab**. Closing closes only that tab.
  The model never sees saved passwords, because Playwright doesn't expose them.
- **Background tasks:** they may browse headlessly but can't hand over or finish (both
  are focus-stealing), so they stop and report.
- **Addresses:** local and private addresses (localhost, RFC 1918, link-local, `.local`)
  are refused unless allowed in Settings; only http(s) pages are opened.

## D78. Browsing skills keep page data out

- **What's learnable:** browse steps are learnable even though they return page content.
  `browser_read` only locates; `ref` arguments become `{kind, name}` locators taken from
  the step's evidence; typed text and URLs go through the usual slot generalizer.
- **Refused:** a step whose target name contains digits or currency symbols ("Select
  6E 201 ₹3,980") was a choice made from the page, so the skill is refused. Any final
  step or hand-over makes the turn unlearnable.
- **On replay:** the commitment rules still run at click time.

## D79. Testing the browser for real

- **Fast layer:** `FakeBrowser` (in-memory pages, every action recorded) runs the whole
  chat path, approvals included.
- **Real layer:** a local test airline (`tests/fake_airline.py`: search → results with a
  prompt-injection line → passengers → payment, a CAPTCHA page, a sign-in page with a
  pre-filled password) runs in real Chrome through the actual worker process, on a
  private Xvfb display for the hand-over window.
- **Pitfalls found:**
  - Chrome inherits `XDG_SESSION_TYPE=wayland` from a desktop session and fails on an
    X-only display; a failed visible relaunch now falls back to headless.
  - Chrome's profile socket path must stay under about 107 bytes, so test profiles use
    short temporary paths.

## D80. Active context is a snapshot at the hotkey, pinned to the chat

- **When it's taken:** at the moment the voice shortcut fires (`/api/voice/command`),
  before anything can change. It runs in the background so listening starts at once, and
  the voice request waits up to 3 s for it.
- **Lifetime:** it applies to the voice request that follows within 90 s, then stays
  pinned to that chat for `context.pin_minutes` (15). "Now summarise it" still means the
  same window after the user has switched away. A new hotkey press or a correction
  replaces it.
- **Typed requests:** they carry no context, because the user is in the chat window.
- **How the model gets it:** as a block in `ModelRequest.context` (like memory). It's not
  stored as chat text, and the system prompt stays byte-stable for caching. Window and
  control ids in it are the hands worker's short ids, so tools target them directly.
- **The target is shown** in the overlay (as soon as the snapshot is taken, while the user
  is still speaking) and as a chip on the user's message (`chat.started.target`).

## D81. "This screen" is enforced, not just suggested

The executor gained a `guard` hook, run before assessment:

- **No display named:** `capture_screen` of "the screen" gets the context's
  `display_id` filled in.
- **Another display named by the model:** refused (shown as blocked), unless the user's
  own words name another display ("other/second/left screen", "screen 2", "both screens").
  The model can't widen the scope on its own.
- **Unplugged display:** "isn't connected anymore", and nothing else is captured in its
  place.
- **Window on another virtual desktop or minimized** (`WindowInfo.on_screen`): no capture;
  its controls can still be read through accessibility.
- **Cropping:** a display is cut out of the whole-desktop image with `crop_box`, which
  accounts for negative origins and HiDPI. On Windows the hands worker opts into
  per-monitor DPI awareness, so all coordinates are physical pixels.

## D82. Corrections re-run the request on the new target

"No, the other one", "the other screen/window", "the left/right screen" and "screen N"
are recognised (`core.context.retarget`, no model call).

- **What changes:** the display moves to the other one (or the named one), with a window
  on it if one is known; "the other window" swaps to another visible window.
- **What runs:** the corrected snapshot is used to re-run the last voice request (within
  90 s). Anything that changes something still asks.

## D83. What's shared, and what never is

- **Selection:** shared by default (`context.selection`). Password fields never yield
  any, and neither does a blocklisted app.
- **Clipboard:** off by default (`context.clipboard`). It's read with stock tools only
  (`wl-paste`/`xclip`/`xsel`, `pbpaste`, Win32).
- **Secrets:** either one is dropped, with a note, when it looks like a card number, an
  API key, a private key, a "password:" line or a long token.
- **Taint:** shared text marks the chat as containing outside content, so changes after
  it ask.
- **Blocklisted focus:** a blocklisted app in focus is named but never read (no window,
  control or selection), and the model is told to say so.

## D84. Where the overlay can't follow, and what was left out

- **Pointer on Wayland:** apps can't ask for the pointer position (XWayland's answer is
  stale), so it's marked unreliable there. "This screen" becomes the focused window's
  screen, and the limit is reported.
- **No overlay available:** core falls back to desktop notifications at the moments that
  matter (listening with the target, a question, the result). The browser tab title shows
  "● Listening".
- **Not built: a tray indicator.** It would need a platform toolkit per OS (Linux
  StatusNotifierItem over D-Bus, Win32 `Shell_NotifyIcon`, macOS `NSStatusItem`); it's
  left for later rather than faked.
- **Following across virtual desktops:**
  - X11 override-redirect windows aren't tied to a desktop.
  - Windows tool windows aren't managed by virtual desktops.
  - macOS needs `canJoinAllSpaces`, which Tk doesn't expose: a manual-test item.

## D85. The redesign: one dark-first token set, and pages instead of dialogs

- **Tokens:** every colour, space and radius lives in `frontend/src/styles/tokens.css`.
  - Dark is the default: bg `#0b0b0c`, panel `#141416`, border `#28282a`, mint
    `#6ee7b7`, amber `#f5b25b`, a muted red.
  - Light has the same structure. Its accents are darkened (mint `#047857`, amber
    `#8a5200`) so text on them still passes AA.
  - The theme switch can choose Auto (follow the OS) as well as Dark or Light. With
    nothing stored it starts dark.
- **Contrast is a test, not a promise:** `src/styles/contrast.test.ts` parses the token
  file and checks every text/background pair at 4.5:1 or more, and focus rings and
  control borders at 3:1 or more, in both themes. A token edit that breaks AA fails CI.
- **Pages, not modal dialogs:**
  - Skills, Background agents, Automations, Memory, Audit log and Settings open in the
    main column, next to the sidebar. A page focuses its heading, and Escape or Close
    goes back to the chat.
  - Escape is handled on the document while the page is open. Focus that has fallen to
    `<body>` (for example after Save disables itself) still closes the page.
  - The chat stays mounted underneath, hidden, so a streaming reply or a pending
    approval isn't lost by looking at Settings.
- **States:** every page that loads shows "Loading …", an empty message, or the error
  with a "Try again" button. These come from shared `useLoad` and `LoadState` helpers,
  so no page can forget one.
- **Right panel:** cost today and this month against their caps (meters change colour
  near and at a cap), the last-ask line, permission states and the voice note.
  - It's hidden below 1180 px. The sidebar then gets an Overview page with the same
    content.
  - Below 720 px everything stacks into one column with no horizontal scroll.

## D86. What the chips and new controls mean

- **"Safe mode on"** describes the rules that always apply. Changes ask first, and sends
  and purchases ask every time. Blocklisted apps are never read, and outside content
  makes later changes ask.
  - It isn't a toggle: there is no unsafe mode to switch to.
  - The chip's tooltip spells this out.
- **"Local only. Not shared."** Data and audio stay on this computer; only Claude
  requests leave it, as the tooltip says.
- **Edit first** (approval card): it denies the request, then puts your original message
  back in the box to change and resend. The model never sees a half-approved call.
- **"Approve and send"** is the label only for external sends. Elsewhere it's "Approve",
  so the button says what happens.
- **"verified" tag:** shown on a step only when the tool reported `verified: true` in its
  evidence, for example a write read back or a control state re-checked. Core forwards
  that as `ToolFinished.verified`; the tag isn't inferred from "no error".
- **Fonts:** DotGothic16 for the brand and headings, and Inter for text. Both are Latin
  subsets bundled locally (66 KB together). The daemon's CSP is `default-src 'self'` and
  there's no telemetry, so no font CDN is used.

## D87. The overlay orb

- **Drawing:** a Tk canvas, not an image or a GPU view, so it costs nothing to ship and
  works wherever Tk does.
  - Idle (listening, silent), it breathes over a 3.2 s cycle.
  - It swells with the voice. The level rises quickly and falls slowly, so it follows
    syllables without jitter.
  - While you speak it sends out soft rings. Tk has no alpha, so they fade by blending
    toward the background.
  - Working pulses faster. Done, error and needs-OK are still.
- **Voice level:** core computes the RMS of the microphone chunks it already receives
  (dBFS on a log scale) and sends only that number (`overlay.level`), at most about 12
  times a second and only while listening. No audio reaches the overlay process.
- **Reduced motion:** the orb becomes a still dot that only changes colour, and the
  window appears without fading. The OS setting is read once at start:
  - GNOME `enable-animations`;
  - macOS `reduceMotion`;
  - Windows `SPI_GETCLIENTAREAANIMATION`.
  `ASSISTANT_REDUCED_MOTION=1/0` overrides it.
- **Fade:** in and out over about 180 ms using window alpha. X11 needs a compositor for
  that. Without one the window simply appears and disappears, which is harmless.
- **The hint line names your shortcut, not keys.** Our voice shortcut is whatever you
  bound in your desktop's keyboard settings, and it's press-again-to-send, not
  hold-to-talk. So the hint says "Press your voice shortcut again to run it" rather than
  guessing "Release Option + Space".
- **One theme:** the overlay uses the dark tokens on every OS. It floats over arbitrary
  apps, and one well-tested contrast set beats following the chat window's theme.

## D88. Spoken requests send themselves after 2 seconds of silence

- **When:** once some words have been heard, the chat window ends the request after 2 s of
  quiet audio, as if the shortcut had been pressed again. Before any words, it never
  sends, so opening the mic and thinking first is safe.
- **Noisy rooms:** a fan or a TV can keep the audio from ever being quiet. So 3 s with no
  new words also ends it. It's a little longer than the silence rule, so silence decides
  whenever it can.
- **Where:** in the browser (`voice/silence.ts`), which owns the microphone. No extra
  messages are sent and nothing changes in the speech worker.
- **Dictation is excluded:** pauses are normal while dictating, so it still waits for
  Done or the shortcut.
- **Setting:** `voice.auto_send_silence` is on by default (Settings → Voice). The overlay
  hint changes to match: "Stop talking to run it, or press your voice shortcut again."

## D89. The API key can live in a `.env` file, and it wins

- **Why:** you asked for one file to paste the key into. The keychain is still supported.
- **Where:** `ASSISTANT_ENV_FILE`, else `.env` in the source checkout, else `.env` in the app
  data folder. The daemon creates an empty template the first time (mode 600), and a
  looser file is tightened to 600 at start.
- **Precedence:** `.env` beats the shell environment, which beats the keychain. The file
  you edit is the one that counts. Settings shows the source as "environment".
- **Never leaks:** it's gitignored, read once at start, added to the log redactor, and
  scrubbed from every worker's environment like any other secret.
- **Errors say what to fix:** an Anthropic 4xx now shows Anthropic's own reason (e.g. the
  credit balance), not just "error 400".

## D90. Hands-free voice: the daemon owns the microphone

- **What:** you say the wake word ("Jarvis" by default, `DEFAULT_WAKE_WORD` in
  `contracts/app.py`), speak, and hear the answer. Then it listens for a follow-up for
  8 seconds without the wake word. "That's all", "thanks" or silence ends it. No window,
  no click.
- **Why the daemon, not the browser:** the chat tab had to stay open and be clicked. A new
  **ears** process (`workers/ears`, sounddevice/PortAudio) owns the mic and speaker, like
  the overlay owns its window. Core decides everything; the ears only move sound.
- **Wake detection:** the speech model already installed (Vosk) transcribes, on this
  computer, only while there's sound. An utterance that doesn't start with the wake word
  is dropped as soon as that's clear (three words), and nothing waits for it to finish.
  - The match is fuzzy for one word ("jarvas") and stricter for two ("jar vis"), so "the
    jarvis project" or "Travis" don't wake it.
  - Below about -48 dBFS nothing is sent to the engine at all, so a quiet room costs next
    to no CPU.
- **When a request is finished:** 2 s of quiet after the words, as in D88. In a noisy room,
  3 s with no new words.
- **Privacy:** nothing heard is stored or logged unless it's addressed to it (a test checks
  the log). Pause everything closes the microphone (the OS indicator goes off), and so
  does turning it off in Settings or having no speech model.
- **Echo:** without a browser there's no echo cancellation, so the microphone is ignored
  while it speaks (plus 0.5 s). Interrupting means the overlay's Stop or the stop shortcut,
  not your voice. Barge-in would need AEC; that's a later step.
- **Limits:** one conversation at a time; the overlay shows it, and conversations are saved
  as chats ("Recent chats").

## D91. Approvals by voice, with a hard floor

- **Ordinary changes** (write, delete) are read back: "Create notes.txt. Say yes or no."
  Any refusal word wins ("yeah no" is no). Two unclear answers, or silence, mean no.
- **Sends, purchases, and anything after outside content** (web pages, files, command
  output: the untrusted flag) can only be approved by a click on the overlay card. A
  misheard word, a TV or a prompt-injected page can never send or pay by voice.
- The overlay card is shown for every approval, so a click always works too.

## D92. The natural voice: Piper as an opt-in, separate GPL program

- **Correction:** M7 rejected Piper because it's GPL-3.0 (it uses espeak-ng). When you
  chose "natural voice", I had wrongly said it was MIT; it isn't.
- **What we do instead:** `python -m core.cli install-voice` shows the sizes and licenses,
  asks, then installs Piper into its own venv in the app data folder. It is run as a
  separate program (text in, audio out); nothing of it is imported or bundled, so this
  program stays MIT. THIRD_PARTY flags it.
- **Voices:** only ones trained on public-domain or CC0 recordings: LJ Speech (default)
  and Joe. Each is pinned by SHA-256. Lessac, Ryan and HFC are excluded because of their
  dataset licenses.
- **Without it:** the computer's own voice (`spd-say`, `say`, System.Speech). It's picked up
  live after installing; no restart.
- **Speaking:** replies are made speakable (markdown, links and code left out). Dates and
  times are said the way people say them. Long replies stop at a sentence end, with "the
  rest is in your chat history".

## D93. The page is voice-first: no chat box by default

- **Why:** you use Jarvis by voice, not as a chat bot. The page had a typing box and a Talk
  button front and centre, so it looked and behaved like one. Its Talk button used the
  browser's microphone and the browser's voice, which has no voices on Linux, so it
  "didn't speak".
- **Now:**
  - The page opens on **Voice**: the orb, what Jarvis is doing right now ("Say “Jarvis”",
    "Listening…", "Speaking…"), things to say, and recent conversations.
  - **History** shows past conversations, read-only.
  - There's no typing box, Talk button or New chat button. Settings → Voice can turn a
    typing box back on, kept per browser; the browser tests open the page with
    `#typing=1`.
- **One voice:** "Read aloud" and every spoken reply go through Jarvis's own voice (Piper
  or the OS voice) via `POST /api/voice/say`. The browser's speech synthesis isn't used.
- **The voice shortcut** (`core.cli voice`) now starts a hands-free conversation, as if you
  said the wake word, instead of opening the browser's microphone.
- **Live:** conversations happen inside Jarvis, not the page, so the page polls the voice
  state every second and reloads the list when something was said.

## D94. Jarvis picks its microphone: static and silence are skipped

- **Why:** on this machine the system default input (the built-in analog jack) is pure static
  at full scale (level 0.73, varying by only ±0.03). The real microphone is the webcam's.
  Jarvis listened to the static and never heard "Jarvis".
- **Automatic choice:** at start, each input is heard for 0.8 s.
  - A loud, flat signal is "static" and near-zero is "silent"; both are skipped.
  - The default wins when it sounds like a room.
  - Settings → Voice can name one (`voice.microphone`); reopening on change is live.
- **Linux:** inputs are PipeWire sources, opened through PipeWire's ALSA device with
  `PIPEWIRE_NODE`, so the microphone stays shared with other apps (no exclusive `hw:`
  device).
- **"Quiet" is per microphone:** a fixed threshold (0.33) sat right at the webcam's
  background (about 0.3). The loop now learns the room's level (the quietest fifth of the
  last 10 s) and treats 0.12 above it as sound. It isn't trusted for the first 3 s, so speech
  heard first isn't learned as the room.
- **Status:** the microphone in use and why are logged once and shown in Settings.

## D95. Not a wake-word-only recogniser: measured, rejected

- A recogniser limited to {jarvis, [unk]} is the usual trick for accents and privacy.
- On Piper-spoken test sentences it fired on "Travis", "Java is", "jars" and "the service"
  (4 of 6 sound-alikes).
- The full recogniser plus the start-of-utterance match rejected all six and caught all
  four real ones, so it stays.
- Fillers the recogniser hears before the name ("a jarvis" for "hey Jarvis", "uh", "um")
  are allowed.

## D96. The overlay is the app: a conversation box, plus chips for background jobs

- Modelled on the behaviour shown in Atticus's demo video, built our own way.
- **The box** (Tk, top-left of the pointer's display):
  - header: the orb, the state and a close button;
  - your words as a bubble, each step ticked off when its tool finishes, and the full
    answer as text (not a status line);
  - follow-ups continue in the same box, and the approval card stays inside it.
- **It stays:** a finished conversation stays until the next one, ✕, or 2 idle minutes;
  it no longer hides after 4 s.
- **Chips** (top-right, their own always-on-top window): each background job shows
  "Working on it: <tool>", "Needs your OK", or the last line of its result. Finished ones go
  after 30 s, or on a click.
  - Runtime broadcasts reach the overlay directly, so this works with no web page open.
  - When a job you saw running finishes and Jarvis is idle, it says so out loud.
- **Window placement on GNOME Wayland:** apps can't move other apps' windows there, so
  "Chrome on the left" needs a small GNOME Shell extension (a later step, with your OK:
  it needs a log-out).

## D97. Arranging windows on every desktop

- **One tool:** `arrange_windows` takes placements such as `{app: "Chrome", slot: "left",
  screen: "builtin"}`.
  - **Slots:** halves, quarters, thirds, two-thirds, centre, full.
  - **Screens are said the way people say them:** current, primary, largest ("my big
    screen"), smallest, builtin ("the laptop"), external, other, left-most/right-most, or a
    number counted left to right.
  - It opens apps that aren't running, then **reads every window back** and reports
    "verified" only if each one landed (with a little slack for apps that size in steps).
- **Backends:**
  - **GNOME (Wayland or X11): our own GNOME Shell extension.** Wayland deliberately doesn't
    let apps move other apps' windows, so window placement has to come from inside the
    compositor.
    - The extension exposes List / Screens / Place / Maximize / Focus on the session bus,
      using work areas from GNOME itself, so the top bar and dock are respected.
    - It's tested for real in a private headless GNOME Shell 46: own D-Bus session, own
      settings and folders, nothing shared with the desktop.
    - `core.cli setup-windows` installs it; GNOME loads it at the next login.
  - **Other X11 desktops:** wmctrl, with xrandr for screens and `_NET_WORKAREA` for panels.
  - **macOS:** System Events via osascript's JavaScript, with NSScreen visible frames,
    converted to top-left coordinates. It needs Accessibility permission for Jarvis.app.
  - **Windows:** Win32 (EnumWindows, SetWindowPos, per-monitor work areas, per-monitor DPI
    aware), through ctypes.
- **Privacy:** the extension shows window titles to callers on your own session bus, just as
  X11 always did. Same-user processes could already read your files, so it isn't a new
  boundary.
- **Not built:** hiding the overlay from screen sharing. That's a macOS window property;
  Wayland screencasts can't exclude a window.

## D98. One command installs it anywhere

- **Commands:** `install.sh` (macOS, Linux) and `install.ps1` (Windows), served from the
  public GitHub repo.
- **Python:** uv, pinned to 0.13.0 with the SHA-256 for each platform written into the
  script, provides a private Python 3.12. No Homebrew, no admin, and the system Python is
  untouched.
- **Jarvis:** the wheel and `requirements.txt` come from the latest GitHub Release and are
  checked against `SHA256SUMS`. The release workflow now publishes rather than drafting,
  since "latest" only sees published releases, and still signs `SHA256SUMS` with sigstore.
- **Then:** the API key goes to the app data `.env` (hidden input, mode 600), the
  recommended speech model is downloaded and switched on, the natural voice is installed
  if you say yes, window placement is set up, start at login is turned on, and it starts.
- **macOS:** a tiny Swift launcher, `Jarvis.app`, is built on your Mac and ad-hoc signed.
  - It starts Python as a child process, so macOS attributes the microphone and
    Accessibility to Jarvis.app and asks once. A bare Python started at login would be
    silently refused the microphone.
  - Its `Info.plist` gives the reasons macOS shows in those prompts.
- **Tested on every push:** CI builds the wheel and runs the real installer on Ubuntu,
  macOS and Windows runners. It installs, starts, checks the page, then uninstalls (and on
  macOS checks Jarvis.app was built).
- **Locally:** tested here on Linux in a throwaway home folder.
- **Can't be tested in CI:** the microphone and Accessibility prompts themselves, which
  need a person at a Mac.

## D99. macOS first: a menu bar app, ⌃⌥Space, Apple's own speech

- **Jarvis.app is Swift** (`backend/core/macos/*.swift`, shipped inside the Python package). It
  is compiled on your Mac by `install.sh` and ad-hoc signed. On every push, CI compiles it on a
  macOS runner and starts it.
  - **Menu bar:** an orb in the menu bar (a ring, filled while busy) with status, Talk,
    Pause listening, Settings…, Show log and Quit. No Dock icon, no window of its own.
  - **⌃⌥Space** through Carbon hot keys, which need no Accessibility permission: press starts
    listening at once (no spoken "Yes?"), and letting go sends what was said with no
    silence wait.
  - **The assistant** (`python -m core`) runs as Jarvis.app's child, restarted if it stops,
    logging to `~/Library/Logs/Jarvis.log`. That's why macOS asks for the microphone, speech
    recognition and Accessibility once, for "Jarvis".
  - **`Jarvis --speech`** is Apple's Speech framework with `requiresOnDeviceRecognition`
    (nothing goes to Apple's servers). The voice worker drives it instead of Vosk
    (`workers/voice/apple.py`), so a Mac downloads no speech model.
- **Speaking:** the Mac's own voices through `say`. Piper isn't offered on macOS.
- **First start:** the box appears with a hello and Jarvis says "Jarvis is ready.", once.
- **Not yet on macOS:** clicking and typing inside other apps (the Accessibility-tree hands
  of M4 are Linux-only). Web tasks (the browser agent) and window arrangement work.

## D100. Lower latency, everywhere

- **Replies are spoken sentence by sentence as they stream.** The first sentence is heard
  while the model is still writing the rest.
  - The splitter never cuts inside a number ("3.5") or a code block.
  - Very short pieces wait for the next sentence.
  - One voice at a time: a reply's sentences, then any question.
- **The talk key's release** ends the request immediately (`finish`), with no 2 s silence
  wait.
- **No spoken "Yes?"** when you start with the key.

## D101. Secrets can't reach GitHub

- `scripts/check_secrets.py` runs as a git pre-commit hook (`scripts/install-hooks.sh`) and
  as a CI job.
- It refuses anything shaped like an Anthropic, GitHub, AWS, Google, Slack or Telegram
  credential, or a private key, and any `.env`, auth token or database file. Clearly fake
  test keys are allowed.
- Keys live only in the app data folder's `.env` (gitignored, mode 600).

## D102. Live until "go offline"

- **Starting:** "Hey Jarvis" (or "OK Jarvis", or the name alone, or ⌃⌥Space) starts a live
  session.
- **While live:**
  - Everything you say is a request: no wake word, no timeout.
  - The box stays up ("Live: just talk"), and the menu bar shows it.
- **Ending:** only a goodbye ends it: "go offline", "go to sleep", "stop listening",
  "that's all (thanks)", "bye", or the menu's Go offline. It replies "Going offline. Say Hey
  Jarvis when you need me." and goes back to waiting.
  - A polite "thanks" gets "You're welcome." and stays live.
- **What's ignored:** stray sounds ("um", "okay").
- **Cost while live:** audio reaches the speech engine only while there is sound, so an idle
  live session costs next to nothing.
- **Setting:** `voice.stay_live` (on by default). Off brings back the 8 s follow-up window.
- **Trade-off:** while live, speech in the room (a TV, other people) is taken as a request.
  Say "go offline" when you step away. Approvals still guard anything that changes, sends
  or pays.

## D103. What we took from studying Atticus (ideas, not code), and what the Mac taught us

You asked us to read Atticus (MIT) for understanding. A separate agent read it and reported
in words; no code was copied.

- **Adopted, in our own implementation:**
  - **Start during the pause:** after 0.7 s of quiet mid-request, the model starts on the
    words so far (wake word stripped). Its tool calls only run if the final words match;
    otherwise the head start is discarded and its cost recorded. Fillers, goodbyes and
    approval answers never start it. It follows the existing "start thinking while I pause"
    setting.
  - **Context at the wake word, not at the end:** this was already in place since M12.
  - **Hint words for Apple's recogniser:** "Jarvis", "Hey Jarvis", "go offline" and the like,
    via `contextualStrings`.
  - **The nicest installed English voice** for `say`: Premium, then Enhanced, preferring this
    Mac's region.
  - **No Bluetooth headset mic by default:** opening it switches the headphones into
    low-quality call mode.
  - **A non-activating, borderless panel** for the box and chips. The overlay process is a
    background accessory: no Dock icon and no "Python" menu, set through the Objective-C
    runtime with ctypes.
- **Already ours, or better:**
  - Replies are spoken sentence by sentence as they stream (Atticus speaks the whole answer
    at the end).
  - The talk key's release sends at once.
  - Haiku first, escalating on multi-step work or failure.
  - Skills replay learned tasks with no AI.
  - Safety is enforced outside the model.
- **Not adopted, because it's fragile:**
  - private SkyLight calls for background input;
  - an undocumented preference to read the voice;
  - browser control through "Allow JavaScript from Apple Events";
  - temp-file handshakes.
- **The macOS live test on a real (GitHub) Mac found and fixed:**
  - title bars on the box;
  - a "Python" Dock icon and menu;
  - grey native buttons;
  - a crash going from a finished box to an approval card (on every OS);
  - the screen-control worker dying on macOS because it imported a Linux-only library.

  It also confirmed that window arrangement works on macOS (verified), and that Jarvis.app
  runs and reaches the speech permission step.


## D104. Talking over Jarvis, and earlier reasoning left out of replayed turns

**Barge-in.** While a request runs (thinking and speaking), a second recogniser stream listens
(`core/hands_free_barge.py`). There's no echo cancellation on PortAudio input, so its own voice
reaches the microphone; what's heard is compared with what it's saying (`is_barge_in`): a cut-in
word ("wait", "stop", "hold on"), the wake word, or three words that aren't its own stop the reply
at once. The last three seconds of sound go to the next request so it's heard from the first
word; "wait" alone just makes it listen. Headphones make it exact. The log records counts only.

**Preserved thinking.** Claude accepts a thinking block back only while the system prompt, tools
and every earlier message are byte-identical to when it was written. Between turns that never
holds here (the system prompt carries the current screen context, screenshots aren't stored for
privacy, old turns fall off the 200-message window), so `Store.history` drops earlier turns'
thinking blocks: a leading run, which the API allows. The current turn's tool loop replays its
own blocks untouched.

## D105. Windows and controls on macOS through Jarvis.app (`Jarvis --hands`)

The Linux hands use AT-SPI; on a Mac the same Hands protocol is served by a Swift helper inside
Jarvis.app using the Accessibility API (AXUIElement) and CGEvent for keys, scrolling and clicks
(`core/macos/Hands.swift`, `HandsActions.swift`; `workers/hands/mac.py` speaks to it). Running
inside Jarvis.app means the one Accessibility permission is for "Jarvis", like the microphone and
speech. Keys go only to a window confirmed to be in front; password fields are never read or typed
into; the blocklist applies by app name. When it isn't allowed yet, the first attempt asks macOS
to show its own prompt, once.

Because Jarvis.app is signed on the Mac itself (ad hoc), macOS ties permissions to that exact
build: the installer now rebuilds it only when its Swift sources change, so updates keep what was
allowed.
