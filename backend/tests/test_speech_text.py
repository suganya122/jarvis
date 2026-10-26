"""The hands-free loop's word rules: the wake phrase, ending a conversation, yes/no for
approvals (a muddled answer must never approve), and what's worth saying aloud."""

import pytest

from core.speech_text import could_be_wake, is_barge_in, is_end_phrase, is_hold, match_wake, speakable, yes_no


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
