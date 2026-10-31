"""Risk from what a control says it does. Labels are untrusted, so this only ever raises risk."""

import re

from contracts.common import MONEY, RiskLevel

_PURCHASE = re.compile(
    r"\b(buy|purchase|pay|payment|pay now|checkout|check out|place (?:your )?order|order now|subscribe|"
    r"donate|book now|booking|reserve|request ride|confirm ride|request uber\w*|request \w+ ride|"
    r"confirm (?:order|payment|purchase|booking|uber\w*|ride|trip)|complete (?:order|purchase|payment)|"
    r"send money|top up|recharge|transfer money|transfer)\b",
    re.I,
)
_SEND = re.compile(r"\b(send|post|publish|share|reply|submit|tweet|upload|forward|invite|email)\b", re.I)
_DESTRUCTIVE = re.compile(
    r"\b(delete|remove|erase|discard|format|wipe|empty|uninstall|reset|overwrite|clear all|destroy|revoke|"
    r"trash|bin)\b",
    re.I,
)
DESTRUCTIVE_KEYS = frozenset(
    {"alt+f4", "ctrl+q", "ctrl+w", "ctrl+shift+w", "shift+delete", "ctrl+shift+delete",
     "meta+q", "meta+w", "meta+backspace", "meta+delete", "shift+meta+backspace", "shift+meta+delete",
     "alt+meta+backspace"}  # macOS: quit, close, move to Trash, empty Trash, delete at once
)  # fmt: skip
FILE_MANAGERS = re.compile(r"finder|files|nautilus|dolphin|explorer|thunar|nemo", re.I)
"""Where Delete on its own deletes the selected files."""
TERMINALS = re.compile(r"\bterminal\b|iterm|gnome-terminal|konsole|alacritty|kitty|wezterm|warp|xterm", re.I)
"""Where Enter runs whatever was typed as a command."""


DESCRIBE_THE_PAYMENT = (
    'this button pays: click it again with "about" saying what it pays, as the screen shows it (the '
    "amount, to whom, and with which card or UPI ID), so the user is asked once, with those details"
)


def click_risk(role: str, name: str, text: str, about: str) -> RiskLevel:
    """A price on a control is shopping (a product, the cart and its total), and a link saying "Pay
    bills" or "Recharge" goes to that page. A button's words ("Pay now"), or the click described as
    paying an amount, make it a payment."""
    if MONEY.search(about):
        return RiskLevel.PURCHASE
    risk = label_risk(name, text, about)
    return RiskLevel.WRITE if risk is RiskLevel.PURCHASE and role == "link" else risk


def label_risk(*labels: str) -> RiskLevel:
    text = " ".join(labels)
    if _PURCHASE.search(text):
        return RiskLevel.PURCHASE
    if _SEND.search(text):
        return RiskLevel.EXTERNAL_SEND
    if _DESTRUCTIVE.search(text):
        return RiskLevel.DESTRUCTIVE
    return RiskLevel.WRITE


MESSAGING = re.compile(
    r"whatsapp|messages|telegram|signal|slack|discord|teams|skype|wechat|messenger|mail|outlook|"
    r"gmail|imessage|\bline\b|zoom|google chat|instagram|linkedin|twitter|\bx\b",
    re.I,
)
"""Apps where Enter sends what was typed to someone."""
SEND_KEYS = frozenset({"enter", "ctrl+enter", "meta+enter", "shift+meta+d"})


def key_risk(keys: list[str], where: str = "") -> RiskLevel:
    combo = "+".join(keys)
    if combo in DESTRUCTIVE_KEYS or (combo in ("delete", "backspace") and FILE_MANAGERS.search(where)):
        return RiskLevel.DESTRUCTIVE
    if combo in SEND_KEYS and MESSAGING.search(where):  # Enter in a chat sends the message
        return RiskLevel.EXTERNAL_SEND
    return RiskLevel.WRITE


def runs_commands(where: str) -> bool:
    """A terminal with a shell in it (not one running Claude Code, whose prompt isn't a shell)."""
    return bool(TERMINALS.search(where)) and "claude" not in where.casefold()
