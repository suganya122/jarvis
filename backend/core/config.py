"""User settings: one validated schema, persisted in SQLite."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from contracts.app import DEFAULT_WAKE_WORD
from contracts.common import Capability
from contracts.model import ModelTier
from contracts.tools import PolicyMode
from models.config import AnthropicConfig, LocalConfig

ProviderName = Literal["anthropic", "local"]

DEFAULT_PORT = 8765


class ServerConfig(BaseModel):
    chat_sends_per_minute: int = Field(default=30, ge=1)
    max_concurrent_chats: int = Field(default=3, ge=1, le=10)


FULL_AUTOMATION = True
"""Out of the box it does what it's asked, sending messages included, and asks only before
paying or deleting (tools.full_automation in Settings turns the stricter behaviour back on)."""
ASK_FIRST = (Capability.PURCHASE,)


def default_policies() -> dict[Capability, PolicyMode]:
    """Like a real assistant: it just does what it's asked. Paying and deleting always ask (that's
    the step's risk, see policy.decide); everything else is allowed unless the user says otherwise."""
    return {c: "ask" if c in ASK_FIRST else "allow" for c in Capability}


def _was_old_default(policies: dict[Capability, PolicyMode]) -> bool:
    """Settings saved with an earlier default (never touched by the user) are upgraded:
    until 0.1.9 everything asked except reading files; until 0.1.15 sending also asked."""
    if not policies:
        return False
    first = all(mode == ("allow" if cap is Capability.FS_READ else "ask") for cap, mode in policies.items())
    second = all(
        mode == ("ask" if cap in (Capability.SEND_EXTERNAL, Capability.PURCHASE) else "allow")
        for cap, mode in policies.items()
    )
    return first or second


DEFAULT_DENIED_PATHS = [
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker",
    "~/.password-store",
    "~/.netrc",
    "~/.config/gcloud",
    "~/.local/share/keyrings",
    "~/Library/Keychains",
]

DEFAULT_BLOCKED_APPS = [
    "keepass",
    "1password",
    "bitwarden",
    "lastpass",
    "dashlane",
    "enpass",
    "seahorse",
    "passwords and keys",
    "keychain access",
    "authenticator",
]

DEFAULT_SHELL_ALLOWLIST = [
    "ls",
    "pwd",
    "whoami",
    "date",
    "uname",
    "echo",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "df",
    "du",
    "which",
    "where",
    "hostname",
    "uptime",
]


class ToolSettings(BaseModel):
    policies: dict[Capability, PolicyMode] = Field(default_factory=default_policies)
    full_automation: bool = Field(default_factory=lambda: FULL_AUTOMATION)
    """On: "just do it". Sending messages and driving other apps don't ask; paying and deleting
    always do. Sends still ask for requests that came from a chat app and for unattended routines.
    Off: every send asks, and so does any app control after reading outside content."""

    @field_validator("policies")
    @classmethod
    def _upgrade_old_default(cls, value: dict[Capability, PolicyMode]) -> dict[Capability, PolicyMode]:
        return default_policies() if _was_old_default(value) else value

    allowed_roots: list[str] = Field(default_factory=lambda: ["~"])
    denied_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_DENIED_PATHS))
    shell_allowlist: list[str] = Field(default_factory=lambda: list(DEFAULT_SHELL_ALLOWLIST))
    approval_timeout_s: float = Field(default=120, ge=10, le=900)
    max_output_chars: int = Field(default=8_000, ge=500, le=100_000)
    command_timeout_s: float = Field(default=20, ge=1, le=300)
    capture_max_edge: int = Field(default=1280, ge=512, le=2000)
    """Screenshots are downscaled to this many pixels on their longest side before Claude sees them."""
    blocked_apps: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCKED_APPS))
    """Apps the assistant never reads or controls (password managers, banking)."""


def default_site_search() -> dict[str, str]:
    return {
        "google": "https://www.google.com/search?q={query}",
        "duckduckgo": "https://duckduckgo.com/?q={query}",
        "youtube": "https://www.youtube.com/results?search_query={query}",
        "wikipedia": "https://en.wikipedia.org/w/index.php?search={query}",
        "github": "https://github.com/search?q={query}",
        "maps": "https://www.openstreetmap.org/search?query={query}",
        "reddit": "https://www.reddit.com/search/?q={query}",
    }


def _check_template(value: str) -> str:
    if not value.startswith(("https://", "http://")) or "{query}" not in value:
        raise ValueError("search URLs must start with http(s):// and contain {query}")
    return value


class RouterSettings(BaseModel):
    enabled: bool = True
    direct_without_asking: bool = True
    """Commands you type yourself (e.g. 'open firefox') skip 'ask'; destructive still asks."""
    search_url: str = "https://duckduckgo.com/?q={query}"
    """Free and keyless. Point it at your own SearXNG instance if you have one."""
    site_search: dict[str, str] = Field(default_factory=default_site_search)

    @field_validator("search_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_template(value)

    @field_validator("site_search")
    @classmethod
    def _sites(cls, value: dict[str, str]) -> dict[str, str]:
        for name, template in value.items():
            if not name.isalnum():
                raise ValueError("site names must be letters and digits")
            _check_template(template)
        return {k.lower(): v for k, v in value.items()}


class AgentSettings(BaseModel):
    max_steps: int = Field(default=15, ge=1, le=50)
    """Model calls per turn; each tool round is one step."""
    max_turn_tokens: int = Field(default=200_000, ge=1_000)
    """Stop the turn once input + output tokens across its steps exceed this."""
    max_identical_calls: int = Field(default=2, ge=1, le=10)
    """Same tool with the same arguments more often than this in one turn = a loop."""
    escalate: bool = True
    """A Cheap turn moves to Balanced (once) when a step fails or the task is multi-step."""
    escalate_after_steps: int = Field(default=3, ge=2, le=50)
    """Tool rounds on Cheap before a turn counts as multi-step."""


class SkillSettings(BaseModel):
    enabled: bool = True
    """Replay learned skills before asking the model."""
    learn: bool = True
    """Record verified tool sequences as skill candidates."""
    promote_after: int = Field(default=2, ge=1, le=10)
    """Verified successes before a skill replays on its own."""
    demote_after_failures: int = Field(default=2, ge=1, le=10)
    replay_without_asking: bool = True
    """Replayed steps skip 'ask' like a direct command; destructive steps always ask."""


class VoiceSettings(BaseModel):
    enabled: bool = True
    stt_model: str | None = None
    """Installed speech model id (see /api/voice/models). None until one is downloaded."""
    speak_replies: bool = False
    """Read replies aloud with the computer's own local voices after a voice request."""
    speculative: bool = True
    """Start the model while you pause; nothing runs until the final transcript matches."""
    auto_send_silence: bool = True
    """Send a spoken request after 2 s of silence (the chat window decides; dictation waits)."""
    hands_free: bool = True
    """Always listen for the wake word and hold a spoken conversation; no window or click needed."""
    wake_word: str = Field(
        default=DEFAULT_WAKE_WORD, min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z' ]*$"
    )
    silence_s: float = Field(default=2.0, ge=0.8, le=6.0)
    """Quiet time after you stop talking before a hands-free request is run."""
    stt_engine: Literal["auto", "apple", "vosk"] = "auto"
    """auto: Apple's on-device recognition on a Mac with Jarvis.app, else the downloaded model."""
    microphone: str = Field(default="", max_length=300)
    """Which input to listen to ("" = automatic: skips inputs that are only static or silence)."""
    echo_cancel: bool = True
    """macOS: listen and speak through Jarvis.app with Apple's echo cancellation, so it doesn't
    hear itself and can be talked over. Off: the plain microphone and the `say` command."""
    stay_live: bool = True
    """After "Hey Jarvis", stay live: everything you say is a request (no wake word) until you
    say "go offline". Off: it listens for follow_up_s after each answer, then waits again."""
    follow_up_s: float = Field(default=8.0, ge=2.0, le=30.0)
    """After a reply, how long it keeps listening for your next sentence (no wake word needed)."""
    dictation_cleanup: Literal["llm", "local", "off"] = "llm"
    overlay: bool = True
    """Show the small always-on-top voice window while a voice request runs."""
    speak_chat_replies: bool = False
    """Also read replies to typed messages aloud (spoken requests use speak_replies)."""
    """llm: one quick cheap-model pass (falls back to local when offline). local: rules only."""


class BackgroundSettings(BaseModel):
    max_concurrent: int = Field(default=2, ge=1, le=5)
    token_budget: int = Field(default=100_000, ge=5_000)
    """Per background task (and per routine run): input + output tokens across all steps."""
    max_steps: int = Field(default=25, ge=1, le=100)
    desktop_notifications: bool = True
    """Also show notifications through the desktop (notify-send), even with the browser closed."""


class BrowserSettings(BaseModel):
    mode: Literal["isolated", "own"] = "isolated"
    """isolated: the assistant's own browser profile. own: your logged-in Chrome (opt in)."""
    show_window: bool = False
    """Watch it work. Off: it browses invisibly and shows the window only to hand over."""
    channel: Literal["auto", "chrome", "msedge", "chromium"] = "auto"
    cdp_url: str = "http://127.0.0.1:9222"
    """Own-browser mode: your Chrome started with --remote-debugging-port=9222."""
    allow_private_hosts: bool = False
    """Allow pages on this computer or the local network (routers, NAS…)."""
    min_seconds_between_pages: float = Field(default=1.0, ge=0.2, le=30)
    """Politeness: at most one page load per site this often."""


class ContextSettings(BaseModel):
    """What the voice hotkey notes about where you are (see core/context.py)."""

    selection: bool = True
    """Share the text selected in the app you're in."""
    clipboard: bool = False
    """Share the clipboard too (off unless you turn it on)."""
    pin_minutes: int = Field(default=15, ge=1, le=240)
    """How long a chat keeps targeting that screen and window."""


class MemorySettings(BaseModel):
    enabled: bool = True
    """Give saved memories to the model. Off: memories are kept but not used."""
    max_prompt_chars: int = Field(default=3_000, ge=200, le=20_000)
    """Budget per request; past it, the memories sharing the most words with the request win."""


class SpendingSettings(BaseModel):
    """Hard caps on Claude spending (estimated from token usage). None = no cap."""

    daily_cap_usd: float | None = Field(default=2.0, ge=0)
    monthly_cap_usd: float | None = Field(default=20.0, ge=0)
    warn_at: float = Field(default=0.8, gt=0, le=1)
    """Show the meter as a warning from this fraction of a cap."""


class PairedAccount(BaseModel):
    user_id: str
    name: str = ""
    paired_at: float


class PluginSettings(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=dict)
    """Plugins start only once the user enables them."""
    options: dict[str, dict[str, str]] = Field(default_factory=dict)
    paired: dict[str, list[PairedAccount]] = Field(default_factory=dict)
    """Chat-app accounts allowed to talk to the assistant, per channel. Everyone else is ignored."""
    messages_per_minute: int = Field(default=10, ge=1, le=120)


class Settings(BaseModel):
    schema_version: Literal[1] = 1
    provider: ProviderName = "anthropic"
    default_tier: ModelTier = ModelTier.CHEAP
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)
    skills: SkillSettings = Field(default_factory=SkillSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    background: BackgroundSettings = Field(default_factory=BackgroundSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    spending: SpendingSettings = Field(default_factory=SpendingSettings)
    plugins: PluginSettings = Field(default_factory=PluginSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
