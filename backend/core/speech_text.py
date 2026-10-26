"""Words in, words out for the hands-free voice loop: the wake phrase, "that's all", yes/no
answers, and turning a reply into something worth saying aloud. All pure functions."""

import re
from difflib import SequenceMatcher

GREETINGS = {"hey", "hi", "hello", "ok", "okay", "yo", "a", "uh", "um", "hmm"}
"""Allowed before the name; the recogniser often hears "hey" as "a"."""
WAKE_SIMILARITY = 0.8
SPLIT_SIMILARITY = 0.85
"""Stricter for two words joined, so "the jarvis" isn't taken for the wake word "jarvis"."""
END_PHRASES = {
    "thats all", "that is all", "that will be all", "thanks", "thank you",
    "stop", "stop listening", "goodbye", "bye", "good bye", "go to sleep", "never mind",
    "nevermind", "cancel", "im done", "i am done", "no thanks", "no thank you",
}  # fmt: skip
CLOSING_WORDS = {
    "thats", "that", "is", "all", "will", "be", "thanks", "thank", "you", "ok", "okay", "bye", "goodbye",
    "good", "im", "i", "am", "done", "no", "nothing", "else", "stop", "listening", "cool", "great",
    "perfect", "cheers", "never", "mind", "nevermind", "for", "now", "much", "very", "go", "to", "sleep",
}  # fmt: skip
"""An utterance made only of these ("that's all, thanks", "no that's it") is a goodbye..."""
CLOSING_STRONG = {"all", "thanks", "thank", "bye", "goodbye", "done", "nothing", "stop", "nevermind", "sleep"}
"""...as long as it has one of these, so "okay" or "no" alone isn't taken as goodbye."""
APPROVE = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "approve", "approved", "confirm", "proceed",
           "affirmative", "please"}  # fmt: skip
APPROVE_PHRASES = ("go ahead", "do it", "go for it")
DENY = {"no", "nope", "nah", "deny", "denied", "cancel", "stop", "dont", "never", "negative", "abort",
        "wait", "not"}  # fmt: skip
MAX_SPOKEN_CHARS = 500


def words(text: str) -> list[str]:
    """Lowercase words with punctuation gone ("What's" -> "whats")."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).split()  # apostrophes drop out too


def _similar(a: str, b: str, threshold: float) -> bool:
    return a == b or SequenceMatcher(None, a, b).ratio() >= threshold


def match_wake(text: str, wake: str) -> tuple[bool, str]:
    """Does the utterance start with the wake word ("jarvis", "hey jarvis")? Returns
    (addressed, the rest). The recogniser may split or bend the name, so the first one or two
    words are compared by similarity."""
    spoken, target = words(text), "".join(words(wake))
    if not spoken or not target:
        return False, ""
    start = 1 if spoken[0] in GREETINGS and len(spoken) > 1 else 0
    for take in (1, 2):
        head = spoken[start : start + take]
        if len(head) == take and _similar(
            "".join(head), target, WAKE_SIMILARITY if take == 1 else SPLIT_SIMILARITY
        ):
            return True, " ".join(spoken[start + take :])
    return False, ""


def could_be_wake(text: str, wake: str) -> bool:
    """Early check on a half-heard utterance: worth waiting for more? True while there are too
    few words to tell, so a long stretch of unrelated talk is dropped as soon as it's clear."""
    spoken = words(text)
    if len(spoken) < 3:
        return True
    return match_wake(" ".join(spoken[:3]), wake)[0]


def is_end_phrase(text: str) -> bool:
    spoken = words(text)
    if " ".join(spoken) in END_PHRASES:
        return True
    return bool(spoken) and set(spoken) <= CLOSING_WORDS and bool(set(spoken) & CLOSING_STRONG)


def yes_no(text: str) -> bool | None:
    """True for an approval, False for a refusal, None when it's unclear. Any refusal word
    wins ("yeah no" is a no), so a muddled answer can't approve something."""
    spoken = words(text)
    joined = " ".join(spoken)
    if any(w in DENY for w in spoken):
        return False
    if any(w in APPROVE for w in spoken) or any(p in joined for p in APPROVE_PHRASES):
        return True
    return None


_FENCE = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
_OFFSET = re.compile(r"\s*\(UTC[+-]\d{2}:?\d{2}\)")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")  # fmt: skip
_MARKUP = re.compile(r"[`*_#>|~]+|^\s*[-+•]\s+|^\s*\d+[.)]\s+", re.MULTILINE)


def speakable(text: str, limit: int = MAX_SPOKEN_CHARS) -> tuple[str, bool]:
    """The reply as plain speech, cut at a sentence end if long. Returns (text, was_cut)."""
    plain = _FENCE.sub(" (code left out) ", text)
    plain = _LINK.sub(r"\1", plain)
    plain = _URL.sub("a link", plain)
    plain = _MARKUP.sub(" ", plain)
    plain = _OFFSET.sub("", plain)  # said as people say them: "26 October 2026, 6:29"
    plain = _DATE.sub(_say_date, plain)
    plain = _TIME.sub(lambda m: f"{int(m.group(1))}:{m.group(2)}", plain)
    plain = " ".join(plain.split())
    if len(plain) <= limit:
        return plain, False
    cut = plain[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0]).strip(), True


def _say_date(match: re.Match[str]) -> str:
    year, month, day = (int(g) for g in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return match.group(0)
    return f"{day} {MONTHS[month - 1]} {year},"
