"""The browser's safety rules (pure) and how the skill learner treats browsing steps."""

import pytest

from contracts.browser import PageElement, PageSnapshot
from contracts.skills import TraceStep
from skills.learner import NotLearnable, learn
from workers.browser.safety import (
    challenge,
    commitment,
    render,
    sends_personal_details,
    sensitive,
    url_problem,
)


def el(kind: str, name: str, **kw: str) -> PageElement:
    return PageElement(ref=kw.pop("ref", "e1"), kind=kind, name=name, **kw)  # type: ignore[arg-type]


EMPTY = PageSnapshot(url="https://shop.test/")


@pytest.mark.parametrize(
    "name",
    [
        "Pay now",
        "Pay ₹4,500",
        "Book now",
        "Confirm booking",
        "Place order",
        "Place your order",
        "Buy now",
        "Complete purchase",
        "Confirm and pay",
        "Submit payment",
        "Subscribe",
        "Delete my account",
    ],
)
def test_commitments_are_recognised(name: str) -> None:
    assert commitment(el("button", name), EMPTY)


@pytest.mark.parametrize(
    "name",
    [
        "Proceed to checkout",
        "Continue to payment",
        "Search flights",
        "Select",
        "View cart",
        "Continue",
        "Book a demo of search",
    ],
)
def test_ordinary_buttons_are_not_commitments(name: str) -> None:
    assert commitment(el("button", name), EMPTY) is None


def test_a_submit_in_a_card_form_is_a_commitment_whatever_its_label() -> None:
    page = PageSnapshot(
        url="https://shop.test/pay",
        elements=(
            el("textbox", "Card number", ref="e1", autocomplete="cc-number", form="f1"),
            el("button", "Continue", ref="e2", input_type="submit", form="f1"),
        ),
    )
    assert commitment(page.elements[1], page) == "“Continue” submits a payment form"


@pytest.mark.parametrize(
    ("element", "why"),
    [
        (el("textbox", "Password", input_type="password"), "a password field"),
        (el("textbox", "Number", autocomplete="cc-number"), "a card or security-code field"),
        (el("textbox", "Code", autocomplete="one-time-code"), "a password or code field"),
        (el("textbox", "CVV"), "a password, card, code or ID field"),
        (el("textbox", "", field_name="passport_no"), "a password, card, code or ID field"),
        (el("textbox", "Email", input_type="email"), None),
        (el("button", "Password help"), None),
    ],
)
def test_sensitive_fields(element: PageElement, why: str | None) -> None:
    assert sensitive(element) == why


def test_challenges_are_recognised_not_bypassed() -> None:
    assert challenge(PageSnapshot(url="u", frames=("https://www.google.com/recaptcha/api2/anchor",))) == (
        "the site shows a CAPTCHA"
    )
    assert "human" in (challenge(PageSnapshot(url="u", text="Please verify you are human to continue")) or "")
    assert "code" in (
        challenge(PageSnapshot(url="u", elements=(el("textbox", "Code", autocomplete="one-time-code"),)))
        or ""
    )
    assert challenge(PageSnapshot(url="u", text="Cheap flights to Goa")) is None


def test_addresses() -> None:
    assert url_problem("https://example.com/a", False) is None
    assert url_problem("file:///etc/passwd", False) == "only http and https pages can be opened"
    for private in (
        "http://localhost:8080",
        "http://192.168.1.1/",
        "http://10.0.0.2",
        "http://[::1]/",
        "http://nas.local",
    ):
        assert "local network" in (url_problem(private, False) or ""), private
    assert url_problem("http://127.0.0.1:9000/", True) is None  # allowed in Settings


def test_personal_details_and_rendering() -> None:
    page = PageSnapshot(
        url="https://air.test/passengers",
        title="Passengers",
        text="Who is flying?",
        elements=(
            el("textbox", "Full name", ref="e1", autocomplete="name", form="p"),
            el("textbox", "Email", ref="e2", input_type="email", form="p"),
            el("textbox", "Password", ref="e3", input_type="password", value=""),
            el("button", "Continue", ref="e4", form="p"),
            el("button", "Pay now", ref="e5"),
        ),
    )
    assert sends_personal_details(page.elements[3], page)
    text = render(page, 100)
    assert "[e3] textbox “Password”  (a password field: the user types this)" in text
    assert "[e5] button “Pay now”  (final step: use browser_finish; the user clicks it)" in text
    assert text.endswith("Who is flying?")


def step(tool: str, name: str = "", **args: object) -> TraceStep:
    evidence: dict[str, object] = {"verified": True, "target": {"element": {"kind": "button", "name": name}}}
    return TraceStep(
        tool=tool, args=dict(args), status="ok", risk="read", trust="untrusted", evidence=evidence
    )


def test_browsing_is_learnable_with_locators_and_parameters() -> None:
    draft = learn(
        "search the shop for blue shoes",
        [
            step("browser_open", url="https://shop.test"),
            step("browser_read"),
            step("browser_type", "Search", ref="e2", text="blue shoes", submit=True),
        ],
        tainted_before=False,
    )
    typed = draft.steps[-1].args_template
    assert typed["ref"] == {"kind": "button", "name": "Search"} and "{" in typed["text"]  # locator + slot


def test_page_specific_choices_and_final_steps_are_never_learned() -> None:
    with pytest.raises(NotLearnable, match="price, date or number"):
        learn(
            "book the cheapest flight",
            [
                step("browser_open", url="https://air.test"),
                step("browser_click", "Select AI 101 ₹4,500", ref="e7"),
            ],
            False,
        )
    with pytest.raises(NotLearnable, match="always the user's"):
        learn("pay for it", [step("browser_finish", "Pay now", ref="e9", what="x", price="1")], False)
