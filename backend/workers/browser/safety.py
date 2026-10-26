"""The browser's safety rules, as pure functions over a page snapshot.

- Commitments (pay, book, place order, confirm, irreversible submit) are never clicked
  by the assistant: it summarises them for approval and the user makes the final click.
- Password, card, one-time-code and ID fields are never typed by the assistant.
- CAPTCHAs, two-factor prompts and bot checks are never bypassed: the user takes over.
- Submitting a form with personal details sends them to the site: that asks first.
"""

import ipaddress
import re
from urllib.parse import urlparse

from contracts.browser import PageElement, PageSnapshot

COMMIT = re.compile(
    r"\b(pay(\s+now)?|pay\s+[$€£₹¥]|place\s+(your\s+)?order|buy(\s+now)?|purchase|complete\s+(purchase|booking|order|payment)|"
    r"confirm\s+(and\s+pay|booking|purchase|payment|order|reservation|transfer)|book\s+(now|it|this)|reserve\s+now|"
    r"submit\s+(payment|order|application)|subscribe|donate|send\s+money|transfer\s+(now|funds)|"
    r"delete\s+(my\s+)?account|start\s+(my\s+)?(subscription|membership)|checkout\s+now)\b",
    re.I,
)
NOT_COMMIT = re.compile(
    r"\b(proceed|continue|go)\s+to\s+(checkout|payment)\b|\bview\s+cart\b|\bsee\s+price", re.I
)
SENSITIVE_NAME = re.compile(
    r"(pass(word|code|phrase)?|\bpin\b|card.?(number|no)|cc.?(num|number)|\bcvv\b|\bcvc\b|security.?code|"
    r"expir|\biban\b|routing|account.?number|\bssn\b|social.?security|passport|national.?id|aadhaar|"
    r"one.?time|\botp\b|verification.?code|2fa)",
    re.I,
)
SENSITIVE_AUTOCOMPLETE = re.compile(r"^(cc-|current-password|new-password|one-time-code)")
PERSONAL_AUTOCOMPLETE = re.compile(r"(email|tel|name|street|address|postal|country|bday|sex)")
PERSONAL_TYPES = frozenset({"email", "tel"})
CHALLENGE_FRAMES = re.compile(
    r"(recaptcha|hcaptcha|turnstile|challenges\.cloudflare|arkoselabs|funcaptcha)", re.I
)
CHALLENGE_TEXT = re.compile(
    r"(verify (that )?you('| a)re (a )?human|i'?m not a robot|unusual traffic|are you a robot|"
    r"complete the captcha|security check|press (and|&) hold|two-?factor|2-step verification|"
    r"enter the (6-digit )?code (we|that we) sent|verification code)",
    re.I,
)


def sensitive(el: PageElement) -> str | None:
    """Why the assistant must never type into this field, if it mustn't."""
    if el.kind not in ("textbox", "select"):
        return None
    if el.input_type == "password":
        return "a password field"
    if SENSITIVE_AUTOCOMPLETE.match(el.autocomplete):
        return (
            "a card or security-code field"
            if el.autocomplete.startswith("cc-")
            else "a password or code field"
        )
    if SENSITIVE_NAME.search(f"{el.name} {el.field_name}"):
        return "a password, card, code or ID field"
    return None


def commitment(el: PageElement, snap: PageSnapshot) -> str | None:
    """Why clicking this would commit the user (pay, book, submit irreversibly), if it would."""
    if el.kind not in ("button", "link"):
        return None
    if NOT_COMMIT.search(el.name):
        return None
    if COMMIT.search(el.name):
        return f"“{el.name}” commits you (payment, booking or a final submit)"
    if el.kind == "button" and el.form and el.input_type in ("submit", ""):
        in_form = [e for e in snap.elements if e.form == el.form]
        if any(sensitive(e) and "card" in (sensitive(e) or "") for e in in_form):
            return f"“{el.name}” submits a payment form"
    return None


def sends_personal_details(el: PageElement, snap: PageSnapshot) -> bool:
    """A submit button whose form holds email/phone/name/address fields."""
    if el.kind != "button" or not el.form:
        return False
    return any(
        e.form == el.form and (e.input_type in PERSONAL_TYPES or PERSONAL_AUTOCOMPLETE.search(e.autocomplete))
        for e in snap.elements
    )


def challenge(snap: PageSnapshot) -> str | None:
    """A CAPTCHA, bot check or two-factor prompt the user has to handle themselves."""
    if any(CHALLENGE_FRAMES.search(f) for f in snap.frames):
        return "the site shows a CAPTCHA"
    if any(e.autocomplete == "one-time-code" for e in snap.elements):
        return "the site asks for a one-time code (two-factor sign-in)"
    if m := CHALLENGE_TEXT.search(snap.text[:3000]):
        phrase = m.group(0).lower()
        if "code" in phrase or "factor" in phrase or "step" in phrase:
            return "the site asks for a verification code (two-factor sign-in)"
        return "the site wants to check you're human"
    return None


def url_problem(url: str, allow_private: bool) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "only http and https pages can be opened"
    host = (parsed.hostname or "").lower()
    if not host:
        return "that address has no host"
    if allow_private:
        return None
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan")):
        return "pages on this computer or the local network can't be opened (see Settings → Browser)"
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return "pages on this computer or the local network can't be opened (see Settings → Browser)"
    return None


def render(snap: PageSnapshot, max_text: int) -> str:
    """The page for the model: interactive elements with refs and notes, then trimmed text."""
    lines = [f"Page: {snap.title or '(no title)'} — {snap.url}"]
    for el in snap.elements:
        desc = f"[{el.ref}] {el.kind} “{el.name}”" if el.name else f"[{el.ref}] {el.kind}"
        if el.kind in ("textbox", "select") and not sensitive(el) and el.value:
            desc += f" = “{el.value}”"
        if el.options:
            desc += " options: " + ", ".join(el.options[:12])
        if el.checked is not None:
            desc += " (checked)" if el.checked else " (not checked)"
        if why := sensitive(el):
            desc += f"  ({why}: the user types this)"
        elif commitment(el, snap):
            desc += "  (final step: use browser_finish; the user clicks it)"
        lines.append(desc)
    if why := challenge(snap):
        lines.append(f"!! {why}: use browser_handover so the user can finish it.")
    text = (
        snap.text
        if len(snap.text) <= max_text
        else snap.text[:max_text] + f" … [{len(snap.text) - max_text} more characters]"
    )
    lines += ["---", text]
    return "\n".join(lines)
