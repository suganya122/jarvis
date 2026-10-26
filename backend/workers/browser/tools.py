"""Browser tools. Every page is untrusted content; the safety rules live in safety.py.

Risk: browsing in the isolated profile only affects that profile (READ), the user's own
logged-in browser is WRITE, sending personal details is EXTERNAL_SEND, and the final
commitment is PURCHASE and is never clicked by the assistant: browser_finish shows the
user the summary, hands them the browser, and they click it themselves.
"""

from pydantic import Field

from contracts.browser import PageElement, PageSnapshot
from contracts.common import Capability, RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolResult
from workers.browser.env import Args, Env, WorkerTool
from workers.browser.safety import (
    challenge,
    commitment,
    render,
    sends_personal_details,
    sensitive,
    url_problem,
)

Ref = str | dict[str, str]
REF_HELP = "An element ref from the latest page ('e12'), as shown by browser_read."
STALE = "no element {ref} on the page now (read the page again with browser_read)"


def _page_result(
    env: Env, snap: PageSnapshot, note: str = "", target: PageElement | None = None
) -> ToolResult:
    env.page = snap
    evidence: dict[str, object] = {"url": snap.url, "verified": True}
    if target is not None:
        evidence["target"] = {"element": target.locator()}
    text = render(snap, env.config.max_text_chars)
    if why := challenge(snap):
        evidence["handover"] = why
        note = (
            f"PAUSED: {why}. I never solve these. Use browser_handover so the user can finish it, "
            "then continue when they say so."
        )
    return ToolResult(
        ok=True, output=f"{note}\n{text}" if note else text, evidence=evidence, trust=Trust.UNTRUSTED
    )


def _risk(env: Env) -> RiskLevel:
    return RiskLevel.WRITE if env.config.mode == "own" else RiskLevel.READ


def _where(env: Env) -> str:
    return "your own browser" if env.config.mode == "own" else "the assistant's browser"


class OpenArgs(Args):
    url: str = Field(max_length=2_000)


class BrowserOpen(WorkerTool[OpenArgs]):
    name = "browser_open"
    description = "Open a web page in the assistant's browser and read it (interactive elements plus text)."
    args_model = OpenArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: OpenArgs, env: Env) -> ToolAssessment:
        url = args.url if "://" in args.url else f"https://{args.url}"
        return ToolAssessment(
            risk=_risk(env),
            summary=f"Open {url} in {_where(env)}",
            error=url_problem(url, env.config.allow_private_hosts),
        )

    async def run(self, args: OpenArgs, env: Env) -> ToolResult:
        url = args.url if "://" in args.url else f"https://{args.url}"
        if problem := url_problem(url, env.config.allow_private_hosts):
            return ToolResult(ok=False, output=f"Not opened: {problem}.")
        await env.polite(url)
        return _page_result(env, await env.browser.open(url))


class NoArgs(Args):
    pass


class BrowserRead(WorkerTool[NoArgs]):
    name = "browser_read"
    description = "Read the current page again: its buttons, links and fields (with refs) and its text."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: NoArgs, env: Env) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Read the page")

    async def run(self, args: NoArgs, env: Env) -> ToolResult:
        if env.page is None:
            return ToolResult(ok=False, output="No page is open yet; use browser_open first.")
        return _page_result(env, await env.browser.snapshot())


class ClickArgs(Args):
    ref: Ref = Field(description=REF_HELP)


class BrowserClick(WorkerTool[ClickArgs]):
    name = "browser_click"
    description = (
        "Click a link or button. Payment, booking, order or other final buttons are refused: "
        "use browser_finish for those so the user reviews and clicks them."
    )
    args_model = ClickArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: ClickArgs, env: Env) -> ToolAssessment:
        el = env.find(args.ref)
        if el is None or env.page is None:
            return ToolAssessment(risk=_risk(env), summary="Click", error=STALE.format(ref=args.ref))
        if why := commitment(el, env.page):
            return ToolAssessment(
                risk=RiskLevel.PURCHASE,
                summary=f"Click “{el.name}”",
                error=f"{why}; use browser_finish so the user reviews it and clicks it themselves",
            )
        if sends_personal_details(el, env.page):
            return ToolAssessment(
                risk=RiskLevel.EXTERNAL_SEND,
                summary=f"Click “{el.name}”: sends the details in this form to {env.host}",
            )
        return ToolAssessment(risk=_risk(env), summary=f"Click “{el.name or el.kind}” on {env.host}")

    async def run(self, args: ClickArgs, env: Env) -> ToolResult:
        el = env.find(args.ref)
        if el is None or env.page is None:
            return ToolResult(ok=False, output="Not clicked: " + STALE.format(ref=args.ref))
        if why := commitment(el, env.page):  # checked again: never click a commitment
            return ToolResult(ok=False, output=f"Not clicked: {why}. Use browser_finish.")
        before = env.page.url
        await env.polite(before)
        snap = await env.browser.click(el.ref)
        return _page_result(env, snap, target=el, note="" if snap.url != before else "(same page)")


class TypeArgs(Args):
    ref: Ref = Field(description=REF_HELP)
    text: str = Field(max_length=2_000)
    submit: bool = Field(default=False, description="Press Enter afterwards (e.g. to search).")


class BrowserType(WorkerTool[TypeArgs]):
    name = "browser_type"
    description = (
        "Type into a field (replacing what's there). Never used for passwords, card numbers or codes."
    )
    args_model = TypeArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: TypeArgs, env: Env) -> ToolAssessment:
        el = env.find(args.ref)
        if el is None or env.page is None:
            return ToolAssessment(risk=_risk(env), summary="Type", error=STALE.format(ref=args.ref))
        if why := sensitive(el):
            return ToolAssessment(
                risk=_risk(env),
                summary=f"Type into “{el.name}”",
                error=f"that's {why}: I never type those; the user types it themselves",
            )
        risk = RiskLevel.EXTERNAL_SEND if args.submit and sends_personal_details(el, env.page) else _risk(env)
        enter = " and press Enter" if args.submit else ""
        return ToolAssessment(
            risk=risk, summary=f"Type “{args.text[:60]}” into “{el.name or 'field'}”{enter}"
        )

    async def run(self, args: TypeArgs, env: Env) -> ToolResult:
        el = env.find(args.ref)
        if el is None:
            return ToolResult(ok=False, output="Not typed: " + STALE.format(ref=args.ref))
        if why := sensitive(el):
            return ToolResult(ok=False, output=f"Not typed: that's {why}.")
        return _page_result(env, await env.browser.fill(el.ref, args.text, args.submit), target=el)


class SelectArgs(Args):
    ref: Ref = Field(description=REF_HELP)
    option: str = Field(max_length=200)


class BrowserSelect(WorkerTool[SelectArgs]):
    name = "browser_select"
    description = "Choose an option in a drop-down list, by its visible text."
    args_model = SelectArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: SelectArgs, env: Env) -> ToolAssessment:
        el = env.find(args.ref)
        if el is None:
            return ToolAssessment(risk=_risk(env), summary="Choose", error=STALE.format(ref=args.ref))
        if el.options and args.option not in el.options:
            return ToolAssessment(
                risk=_risk(env), summary="Choose", error=f"“{args.option}” isn't one of the options"
            )
        return ToolAssessment(risk=_risk(env), summary=f"Choose “{args.option}” in “{el.name}”")

    async def run(self, args: SelectArgs, env: Env) -> ToolResult:
        el = env.find(args.ref)
        if el is None:
            return ToolResult(ok=False, output="Not chosen: " + STALE.format(ref=args.ref))
        return _page_result(env, await env.browser.select(el.ref, args.option), target=el)


class BrowserBack(WorkerTool[NoArgs]):
    name = "browser_back"
    description = "Go back to the previous page."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: NoArgs, env: Env) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Go back")

    async def run(self, args: NoArgs, env: Env) -> ToolResult:
        return _page_result(env, await env.browser.back())


class FinishArgs(Args):
    ref: Ref = Field(description="The final button (pay, book, place order…).")
    what: str = Field(max_length=300, description="What is being bought or booked.")
    price: str = Field(max_length=100, description="Total price with currency, as the page shows it.")
    dates: str = Field(default="", max_length=200)
    passengers: str = Field(default="", max_length=300, description="Who it's for (names, count).")
    refund_terms: str = Field(
        default="", max_length=300, description="Refund/cancellation terms, or 'not shown'."
    )


class BrowserFinish(WorkerTool[FinishArgs]):
    name = "browser_finish"
    description = (
        "The last step of a purchase or booking: show the user a summary for approval, then hand them the "
        "browser with the final button highlighted. The user clicks it; the assistant never does."
    )
    args_model = FinishArgs
    risk = RiskLevel.PURCHASE
    capabilities = (Capability.BROWSER, Capability.PURCHASE)

    def assess(self, args: FinishArgs, env: Env) -> ToolAssessment:
        el = env.find(args.ref)
        if el is None or env.page is None:
            return ToolAssessment(risk=self.risk, summary="Final step", error=STALE.format(ref=args.ref))
        if not commitment(el, env.page):
            return ToolAssessment(
                risk=self.risk,
                summary="Final step",
                error=f"“{el.name}” isn't a final step; use browser_click",
            )
        lines = [f"Final step on {env.host}: “{el.name}”", f"What: {args.what}", f"Price: {args.price}"]
        lines += [
            f"{k}: {v}"
            for k, v in (
                ("Dates", args.dates),
                ("Passengers", args.passengers),
                ("Refund terms", args.refund_terms or "not shown"),
            )
            if v
        ]
        lines.append("Approving opens the browser with the button highlighted. You make the final click.")
        return ToolAssessment(risk=self.risk, summary="\n".join(lines))

    async def run(self, args: FinishArgs, env: Env) -> ToolResult:
        el = env.find(args.ref)
        if el is None:
            return ToolResult(ok=False, output="Not handed over: " + STALE.format(ref=args.ref))
        await env.browser.show(el.locator())
        return ToolResult(
            ok=True,
            output=(
                f"Handed over. The browser window shows “{el.name}” highlighted. The user checks the details "
                "and clicks it themselves; I won't click it. Ask them to tell you when it's done."
            ),
            evidence={"handover": "final step", "irreversible": True},
        )


class HandoverArgs(Args):
    reason: str = Field(max_length=200, description="What the user needs to do, e.g. 'solve the CAPTCHA'.")


class BrowserHandover(WorkerTool[HandoverArgs]):
    name = "browser_handover"
    description = (
        "Show the browser window to the user so they can do a step themselves (CAPTCHA, sign-in, code)."
    )
    args_model = HandoverArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: HandoverArgs, env: Env) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary=f"Show you the browser: {args.reason}")

    async def run(self, args: HandoverArgs, env: Env) -> ToolResult:
        await env.browser.show(None)
        return ToolResult(
            ok=True,
            evidence={"handover": args.reason},
            output=f"Handed over: {args.reason}. Waiting for the user to say they're done.",
        )


class BrowserClose(WorkerTool[NoArgs]):
    name = "browser_close"
    description = "Close the assistant's browser."
    args_model = NoArgs
    risk = RiskLevel.READ
    capabilities = (Capability.BROWSER,)

    def assess(self, args: NoArgs, env: Env) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="Close the browser")

    async def run(self, args: NoArgs, env: Env) -> ToolResult:
        await env.close()
        return ToolResult(ok=True, output="Closed.")


ALL = (
    BrowserOpen,
    BrowserRead,
    BrowserClick,
    BrowserType,
    BrowserSelect,
    BrowserBack,
    BrowserFinish,
    BrowserHandover,
    BrowserClose,
)
