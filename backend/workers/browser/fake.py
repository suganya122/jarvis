"""An in-memory browser for tests: pages are plain data, every action is recorded."""

from dataclasses import dataclass, field

from contracts.browser import PageElement, PageSnapshot


@dataclass
class FakeEl:
    kind: str
    name: str
    go: str | None = None
    """Where clicking (or submitting) this leads."""
    input_type: str = ""
    autocomplete: str = ""
    field_name: str = ""
    form: str = ""
    options: tuple[str, ...] = ()


@dataclass
class FakePage:
    title: str
    text: str
    elements: list[FakeEl] = field(default_factory=list)
    frames: tuple[str, ...] = ()


class FakeBrowser:
    def __init__(self, site: dict[str, FakePage]) -> None:
        self.site = site
        self.url = "about:blank"
        self.history: list[str] = []
        self.values: dict[str, str] = {}
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.shown: list[dict[str, str] | None] = []
        self.closed = False

    def _page(self) -> FakePage:
        return self.site.get(self.url.split("?")[0], FakePage("Not found", "404"))

    async def snapshot(self) -> PageSnapshot:
        page = self._page()
        elements = tuple(
            PageElement(
                ref=f"e{i}",
                kind=e.kind,
                name=e.name,
                input_type=e.input_type,
                autocomplete=e.autocomplete,
                field_name=e.field_name,
                form=e.form,
                options=e.options,
                value="" if e.input_type == "password" else self.values.get(f"{self.url}#{e.name}", ""),
            )
            for i, e in enumerate(page.elements, start=1)
        )
        return PageSnapshot(
            url=self.url, title=page.title, text=page.text, elements=elements, frames=page.frames
        )

    def _el(self, ref: str) -> FakeEl:
        return self._page().elements[int(ref[1:]) - 1]

    def _go(self, url: str) -> None:
        self.history.append(self.url)
        self.url = url

    async def open(self, url: str) -> PageSnapshot:
        self._go(url)
        return await self.snapshot()

    async def click(self, ref: str) -> PageSnapshot:
        el = self._el(ref)
        self.clicks.append(el.name)
        if el.go:
            self._go(el.go)
        return await self.snapshot()

    async def fill(self, ref: str, text: str, submit: bool) -> PageSnapshot:
        el = self._el(ref)
        self.fills.append((el.name, text))
        self.values[f"{self.url}#{el.name}"] = text
        if submit and el.go:
            self._go(el.go)
        return await self.snapshot()

    async def select(self, ref: str, option: str) -> PageSnapshot:
        return await self.fill(ref, option, submit=False)

    async def back(self) -> PageSnapshot:
        if self.history:
            self.url = self.history.pop()
        return await self.snapshot()

    async def show(self, highlight: dict[str, str] | None) -> None:
        self.shown.append(highlight)

    async def close(self) -> None:
        self.closed = True
