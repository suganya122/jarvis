"""Risk from what a control says it does. Labels are untrusted, so this only ever raises risk."""

import re

from contracts.common import RiskLevel

_PURCHASE = re.compile(
    r"\b(buy|purchase|pay|checkout|check out|place order|order now|subscribe|donate)\b", re.I
)
_SEND = re.compile(r"\b(send|post|publish|share|reply|submit|tweet|upload|forward|invite|email)\b", re.I)
_DESTRUCTIVE = re.compile(
    r"\b(delete|remove|erase|discard|format|wipe|empty|uninstall|reset|overwrite|clear all|destroy|revoke)\b",
    re.I,
)
DESTRUCTIVE_KEYS = frozenset(
    {"alt+f4", "ctrl+q", "ctrl+w", "ctrl+shift+w", "shift+delete", "ctrl+shift+delete"}
)


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
    if combo in DESTRUCTIVE_KEYS:
        return RiskLevel.DESTRUCTIVE
    if combo in SEND_KEYS and MESSAGING.search(where):  # Enter in a chat sends the message
        return RiskLevel.EXTERNAL_SEND
    return RiskLevel.WRITE
