"""The hands-free loop's word rules: the wake phrase, ending a conversation, yes/no for
approvals (a muddled answer must never approve), and what's worth saying aloud."""

import pytest

from core.speech_text import (
    could_be_wake,
    end_of_speech_s,
    is_barge_in,
    is_end_phrase,
    is_hold,
    match_wake,
    speakable,
    without_echo,
    yes_no,
)


@pytest.mark.parametrize(
    ("heard", "rest"),
    [
        ("jarvis what time is it", "what time is it"),
        ("hey jarvis open chrome", "open chrome"),
        ("okay jarvis", ""),
        ("Jarvis, what's the weather?", "whats the weather"),
        ("jar vis play some music", "play some music"),  # the recogniser split the name
        ("jarvas open my email", "open my email"),  # a near miss
    ],
)
def test_wake_phrase_matches(heard: str, rest: str) -> None:
    assert match_wake(heard, "jarvis") == (True, rest)


@pytest.mark.parametrize(
    "heard",
    [
        "what time is it",
        "travel plans for tomorrow",
        "service is down again",
        "",
        "hey there",
        "the jarvis project",
    ],
)
def test_other_talk_is_not_the_wake_phrase(heard: str) -> None:
    assert match_wake(heard, "jarvis")[0] is False


def test_the_wake_word_is_a_setting() -> None:
    assert match_wake("computer lights off", "computer") == (True, "lights off")
    assert match_wake("computer lights off", "jarvis")[0] is False
    assert match_wake("anything", "")[0] is False


def test_long_unrelated_talk_is_dropped_early() -> None:
    assert could_be_wake("jarvis", "jarvis")
    assert could_be_wake("hey", "jarvis")  # too few words to tell yet
    assert could_be_wake("hey jarvis open", "jarvis")
    assert not could_be_wake("and then he said that we should", "jarvis")


@pytest.mark.parametrize(
    "text",
    [
        "that's all",
        "Thanks!",
        "thank you",
        "go to sleep",
        "never mind",
        "that's all thanks",
        "no that's all",
        "okay thank you very much",
        "no nothing else",
        "stop listening",
    ],
)
def test_end_phrases(text: str) -> None:
    assert is_end_phrase(text)


@pytest.mark.parametrize(
    "text", ["thanks for the file, now open it", "what is all this", "open the stop menu"]
)
def test_ordinary_requests_do_not_end_the_conversation(text: str) -> None:
    assert not is_end_phrase(text)


@pytest.mark.parametrize(
    ("heard", "answer"),
    [
        ("yes", True),
        ("yeah sure", True),
        ("go ahead", True),
        ("yes please", True),
        ("no", False),
        ("nope", False),
        ("no don't", False),
        ("yeah no", False),  # a muddled answer is a no
        ("yes wait", False),
        ("cancel that", False),
        ("hmm let me think", None),
        ("", None),
    ],
)
def test_yes_no(heard: str, answer: bool | None) -> None:
    assert yes_no(heard) is answer


def test_replies_are_made_speakable() -> None:
    text = (
        "# Done\n\nHere is the **plan**:\n- open [the docs](https://example.com/x)\n- run `ls -la`\n\n"
        "```py\nprint(1)\n```\nSee https://example.com/long/path for more."
    )
    spoken, cut = speakable(text)
    assert not cut
    for noise in ("#", "**", "`", "](", "http", "print(1)"):
        assert noise not in spoken
    assert "the docs" in spoken
    assert "code left out" in spoken
    assert "a link" in spoken


def test_long_replies_are_cut_at_a_sentence_end() -> None:
    sentence = "This is one fairly ordinary sentence about nothing. "
    spoken, cut = speakable(sentence * 40, limit=200)
    assert cut
    assert spoken.endswith("nothing.")
    assert len(spoken) <= 200
    assert speakable("Short.")[0] == "Short."


def test_dates_and_times_are_said_the_way_people_say_them() -> None:
    spoken, _ = speakable("It's Monday 2026-10-26 06:29:32 IST (UTC+0530).")
    assert spoken == "It's Monday 26 October 2026, 6:29 IST."
    assert speakable("Meeting at 14:05 on 2026-01-03")[0] == "Meeting at 14:05 on 3 January 2026,"
    assert speakable("Version 2026-13-40 stays")[0] == "Version 2026-13-40 stays"


def test_a_streaming_reply_is_cut_into_sentences_as_it_arrives() -> None:
    from core.speech_text import SentenceSplitter

    splitter = SentenceSplitter()
    assert splitter.push("It's sunny today in Chen") == []
    assert splitter.push("nai, about 31 degrees. Tomorrow looks ") == [
        "It's sunny today in Chennai, about 31 degrees."
    ]
    assert splitter.push("similar, with rain at 3.5 pm maybe.") == []  # "3.5" isn't a sentence end
    assert splitter.flush() == "Tomorrow looks similar, with rain at 3.5 pm maybe."
    assert splitter.flush() == ""


def test_short_bits_and_code_blocks_wait() -> None:
    from core.speech_text import SentenceSplitter

    splitter = SentenceSplitter()
    assert splitter.push("Done. ") == []  # too short alone: joins the next sentence
    assert splitter.push("I saved the file to your Documents folder. ") == [
        "Done. I saved the file to your Documents folder."
    ]
    assert splitter.push("Run this:\n```\nls -la. \n```\nThat lists every file in the folder. ") == [
        "Run this:\n```\nls -la. \n```\nThat lists every file in the folder."
    ]


@pytest.mark.parametrize(
    "text",
    [
        "go offline",
        "Go offline.",
        "okay jarvis go offline",
        "go to sleep",
        "stop listening",
        "that's all",
        "That's all, thanks.",
        "no that's all",
        "okay bye",
        "I'm done",
    ],
)
def test_go_offline(text: str) -> None:
    from core.speech_text import is_go_offline

    assert is_go_offline(text)


@pytest.mark.parametrize(
    "text", ["thanks", "thank you very much", "go to the offline store", "what time is it"]
)
def test_not_go_offline(text: str) -> None:
    from core.speech_text import is_go_offline

    assert not is_go_offline(text)


def test_fillers_are_not_requests() -> None:
    from core.speech_text import is_filler

    assert is_filler("um") and is_filler("Okay.") and is_filler("uh the") and is_filler("")
    assert not is_filler("ok open chrome")


@pytest.mark.parametrize(
    ("heard", "saying", "cut_in"),
    [
        ("ok wait", "You are on the left screen.", True),
        ("no the other screen please", "You are on the left screen.", True),
        ("jarvis", "Sure, it is noon.", True),
        ("you are on the left", "You are on the left screen.", False),  # its own voice
        ("you are on the lot screen", "You are on the left screen.", False),  # misheard echo
        ("um", "", False),
    ],
)
def test_cut_in_is_told_apart_from_its_own_voice(heard: str, saying: str, cut_in: bool) -> None:
    assert is_barge_in(heard, saying, "jarvis") is cut_in


def test_hold_phrases_are_not_requests() -> None:
    assert is_hold("wait") and is_hold("ok hold on a second") and is_hold("stop")
    assert not is_hold("wait what time is it") and not is_hold("")


FLIGHTS = (
    "Google Flights shows plenty of nonstop Bengaluru to Chennai flights. The cheapest round trip "
    "is about 7,762 rupees, for a departure on Thursday, October 29."
)


@pytest.mark.parametrize(
    ("heard", "cut_in"),
    [
        # its own reply coming back, misheard in places: never a cut-in (it once took
        # "departure on thursday 29th of" for a request)
        ("google flights shows plenty of nonstop bangalore to chennai flights the cheapest round trip "
         "is about 7762 rupees for a departure on thursday 29th of", False),
        ("google flight show plenty of non stop bengal oru to chennai flight the cheapest "
         "around trip", False),
        ("google flights shows plenty of nonstop wait", True),
        ("google flights shows plenty no i want one way only", True),
        ("google flights shows plenty of nonstop bengaluru hey jarvis", True),
    ],
)  # fmt: skip
def test_a_long_reply_coming_back_is_not_a_cut_in(heard: str, cut_in: bool) -> None:
    assert is_barge_in(heard, FLIGHTS, "jarvis") is cut_in


def test_the_request_after_a_cut_in_loses_only_its_own_last_words() -> None:
    assert (
        without_echo("for a departure on thursday flights to delhi please", FLIGHTS)
        == "flights to delhi please"
    )
    assert without_echo("no wait the other screen", FLIGHTS) == "no wait the other screen"
    assert without_echo("anything", "") == "anything"


def test_a_short_answer_that_repeats_its_question_is_the_answer() -> None:
    """It asked "Developer Mode in System Settings, or Xcode?"; the person said "system settings" and
    its echo filter threw that away as its own words (the conversation then lost its thread). A short
    utterance is never dropped on its own, and only the end of what it said can be echo."""
    question = (
        "Do you want Developer Mode in System Settings, or Developer settings for Xcode or the "
        "Terminal? I'm not sure which you mean, so I'll check whichever you choose."
    )
    assert without_echo("system settings", question) == "system settings"
    assert without_echo("safari", "Which browser, Chrome or Safari?") == "safari"
    assert (
        without_echo("four minutes", "Blinding Lights, 4 minutes, 23 seconds. Say yes or no.")
        == "four minutes"
    )
    assert without_echo("yes", "Click the video. Say yes or no.") == "yes"


def test_a_long_stretch_of_its_own_last_words_is_still_removed() -> None:
    question = "Click The Weeknd, Blinding Lights, 4 minutes, 23 seconds on youtube. Say yes or no."
    assert without_echo("say yes or no yes", question) == "yes"  # its tail, then the answer
    assert without_echo("23 seconds on youtube say yes or no", question) == ""  # nothing but its echo


def test_a_long_or_unfinished_sentence_gets_a_longer_pause() -> None:
    assert end_of_speech_s("what time is it", 2.0) == 2.0
    assert end_of_speech_s("open my calendar and", 2.0) == 4.0  # still thinking
    assert end_of_speech_s("send a message to the", 2.0) == 4.0
    long = "please find the email from my manager about the project review and reply that I agree"
    assert end_of_speech_s(long, 2.0) == 3.0
    assert end_of_speech_s("", 2.0) == 2.0


def test_a_recogniser_that_starts_over_after_a_pause_keeps_what_was_said_before_it() -> None:
    from core.speech_text import Transcript

    said = Transcript()
    assert said.update("send a message to") == "send a message to"
    assert said.update("send a message to srinith") == "send a message to srinith"  # it grows
    assert said.update("send a message to Srinith and") == "send a message to Srinith and"  # revised
    # a pause, and the engine's text starts over with only what was said after it:
    assert said.update("tell him") == "send a message to Srinith and tell him"
    assert said.update("tell him I will be late") == "send a message to Srinith and tell him I will be late"
    assert (
        said.update("") == "send a message to Srinith and tell him I will be late"
    )  # an empty one changes nothing
    # a correction that drops one trailing word is not a restart:
    other = Transcript()
    other.update("open the calendar please")
    assert other.update("open the calendar") == "open the calendar"
