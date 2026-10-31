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
MAX_SPOKEN_CHARS = 600


def words(text: str) -> list[str]:
    """Lowercase words with punctuation gone ("What's" -> "whats")."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).split()  # apostrophes drop out too


LONG_WORDS = 12
UNFINISHED = frozenset(
    "and but or so because cause to the a an of for with that which who if then when about um uh uhm "
    "er like my your our his her their also plus".split()
)
"""A pause after one of these ("…and", "…the") is someone thinking, not someone who has finished."""


def end_of_speech_s(text: str, silence_s: float) -> float:
    """How long a pause ends what they're saying. Longer once they've said a lot (people stop to
    think in a long request), and twice as long when the last word leaves the sentence open."""
    said = words(text)
    if said and said[-1] in UNFINISHED:
        return silence_s * 2
    return silence_s * 1.5 if len(said) >= LONG_WORDS else silence_s


class Transcript:
    """What the recogniser has heard so far, kept whole across its restarts.

    Apple's on-device recogniser sometimes starts its text over after a pause: the words before the
    gap disappear from the partial result and only what comes after is left. A new result that is
    clearly shorter and doesn't start where the last one did is such a restart, not a correction,
    so what was heard before it is kept in front."""

    def __init__(self) -> None:
        self._kept = ""
        self._last = ""

    def update(self, raw: str) -> str:
        raw = raw.strip()
        if raw:
            before, now = words(self._last), words(raw)
            if before and len(now) < len(before) - 1 and (now[0] != before[0] or len(now) * 2 <= len(before)):
                self._kept = f"{self._kept} {self._last}".strip()
            self._last = raw
        return f"{self._kept} {self._last}".strip()


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


YES_MAX_WORDS = 12
"""A yes is short ("sure, try that", "I approved it already"): longer is a new request."""


def is_yes_to(question: str, answer: str) -> bool:
    """Is `answer` a yes to `question`, the last thing it said, if that asked something?"""
    asked = question.rstrip(" \n\"'”*)").endswith("?")
    return asked and len(words(answer)) <= YES_MAX_WORDS and yes_no(answer) is True


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


_SENTENCE_END = re.compile(r"[.!?…](?=\s)|\n\s*\n")


class SentenceSplitter:
    """Cuts a streaming reply into sentences, so the first one can be spoken while the rest is
    still being written. Never splits inside a code block or a number ("3.5"); very short pieces
    ("Hi.") wait for the next sentence, so speech doesn't stutter."""

    MIN_CHARS = 24

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        self._buffer += text
        ready: list[str] = []
        start = 0
        for match in _SENTENCE_END.finditer(self._buffer):
            end = match.end()
            piece = self._buffer[start:end]
            if self._buffer[:end].count("```") % 2:  # inside a code block: wait for it to close
                continue
            if len(piece.strip()) < self.MIN_CHARS:
                continue
            ready.append(piece.strip())
            start = end
        self._buffer = self._buffer[start:]
        return ready

    def flush(self) -> str:
        rest, self._buffer = self._buffer.strip(), ""
        return rest


OFFLINE_PHRASES = {
    "go offline", "offline", "go to standby", "standby", "go to sleep", "sleep", "stop listening",
    "thats all", "that is all", "that will be all", "goodbye", "good bye", "bye", "you can go",
    "go offline now", "turn off", "stop",
}  # fmt: skip
"""In a live session, only these end it (back to waiting for "Hey Jarvis")."""
FILLERS = {
    "um",
    "uh",
    "hmm",
    "hm",
    "ah",
    "oh",
    "ok",
    "okay",
    "yeah",
    "mm",
    "huh",
    "the",
    "a",
    "so",
    "and",
    "er",
}


GOODBYE_WORDS = {"all", "bye", "goodbye", "offline", "sleep", "standby", "done", "nothing"}
"""In a closing phrase, these make it a goodbye ("that's all, thanks"); plain "thanks" isn't one."""


def is_go_offline(text: str) -> bool:
    spoken = words(text)
    joined = " ".join(spoken)
    if joined in OFFLINE_PHRASES:
        return True
    if spoken[-2:] == ["go", "offline"] and len(spoken) <= 5:  # "okay jarvis go offline"
        return True
    return is_end_phrase(text) and bool(set(spoken) & GOODBYE_WORDS)


def is_filler(text: str) -> bool:
    """Not a request: a stray "um", "okay", or a word the recogniser made of a noise."""
    spoken = words(text)
    return not spoken or all(w in FILLERS for w in spoken)


ECHO_SIMILARITY = 0.6
"""A heard word this close to one it said in the same place is its own voice, misheard."""
ORDINAL = re.compile(r"^(\d+)(st|nd|rd|th)$")
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
_TEENS_AND_TENS = "sixteen:16 seventeen:17 eighteen:18 nineteen:19 twenty:20 thirty:30 forty:40 fifty:50"
NUMBER_WORDS = {w: str(n) for n, w in enumerate(_ONES.split())} | dict(
    pair.split(":") for pair in f"{_TEENS_AND_TENS} sixty:60 seventy:70 eighty:80 ninety:90".split()
)
"""Spoken numbers as digits: the recogniser may write "four" for the "4" it said."""
CUT_IN_WORDS = {"no", "wait", "stop", "hold", "pause", "enough", "quiet", "cancel", "shut", "hang"}
"""Said over the reply, any one of these cuts it off, like it would a person."""
HOLD_WORDS = CUT_IN_WORDS | {"on", "a", "second", "sec", "minute", "moment", "just", "please", "up", "no"}


def _sounds_like(a: str, b: str) -> bool:
    a, b = ORDINAL.sub(r"\1", a), ORDINAL.sub(r"\1", b)  # "29th" said as "29"
    a, b = NUMBER_WORDS.get(a, a), NUMBER_WORDS.get(b, b)  # "four" heard for "4"
    if a == b:
        return True
    return len(a) > 2 and len(b) > 2 and (a.rstrip("s") == b.rstrip("s") or _similar(a, b, ECHO_SIMILARITY))


def their_words(heard: str, saying: str) -> list[str]:
    """The words in `heard` that are the person's, not its own voice coming back.

    What comes back through the microphone follows what it's saying, in order, sometimes misheard
    ("29th" for "29", "bangalore" for "bengaluru"). So the two are lined up: a heard word counts as
    echo when it matches, or sounds like, the word it said in that same place. Everything else is
    someone else talking, including words heard where it hadn't got to yet."""
    heard_words, own = words(heard), words(saying)
    theirs: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, heard_words, own, autojunk=False).get_opcodes():
        if tag == "delete":  # heard, with nothing it said in that place
            theirs += heard_words[i1:i2]
        elif tag == "replace":
            for k, w in enumerate(heard_words[i1:i2]):
                near = own[max(j1, j1 + k - 1) : min(j2, j1 + k + 2)]  # the word said there, give or take one
                if not any(_sounds_like(w, o) for o in near):
                    theirs.append(w)
    return theirs


def is_barge_in(heard: str, saying: str, wake_word: str, stop_only: bool = False) -> bool:
    """Is someone talking over the reply? A cut-in word ("wait") or the name it didn't say itself,
    or three words of their own; its own voice coming back, however misheard, doesn't count.
    stop_only: it's doing what they asked (building, running a command), and only a cut-in word
    stops that: "are you done yet?" or talk in the room mustn't throw the work away."""
    theirs = their_words(heard, saying)
    if any(w in CUT_IN_WORDS for w in theirs):
        return True
    if stop_only:
        return False
    if wake_word in theirs:
        return True
    return len([w for w in theirs if len(w) > 2 and w not in FILLERS]) >= 3


ECHO_MIN_RUN = 3
"""Fewest words that can be taken for its own voice coming back."""
ECHO_TAIL_WORDS = 4
"""Its echo is the end of what it said: the matched stretch must reach within this many words of it."""
ECHO_ALONE_RUN = 5
"""A whole utterance is thrown away as echo only if it is at least this long: a short one ("system
settings", "safari please") that repeats its question is the person answering."""


def without_echo(heard: str, saying: str) -> str:
    """The start of `heard` minus its own last words still coming back through the microphone.

    Only the end of what it said can arrive late, so only a stretch of three or more words that
    reaches the end of its sentence counts, and never a short utterance on its own: a person
    answering "System Settings or Xcode?" with "system settings" must be heard."""
    rest, own = words(heard), words(saying)
    runs = [
        r
        for start in range(len(own))
        if (r := _run(rest, own, start)) and len(own) - (start + r) <= ECHO_TAIL_WORDS
    ]
    run = max(runs, default=0)
    if run < ECHO_MIN_RUN or (run == len(rest) and run < ECHO_ALONE_RUN):
        return " ".join(rest)
    return " ".join(rest[run:])  # one stretch only: the echo is what came just before they spoke


def _run(heard: list[str], own: list[str], start: int) -> int:
    """How many of `heard`'s first words follow `own` from `start`, one for one."""
    n = 0
    while n < len(heard) and start + n < len(own) and _sounds_like(heard[n], own[start + n]):
        n += 1
    return n


def is_hold(text: str) -> bool:
    """Only "wait", "hold on a second", "stop": go quiet and listen, there's no request yet."""
    spoken = words(text)
    return bool(spoken) and all(w in HOLD_WORDS or w in FILLERS for w in spoken)
