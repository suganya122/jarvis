# Contributing

## Clean-room rule

Do not copy, paraphrase, or port code, prompts, UI, assets, or file structure from other
assistant projects. Design your change yourself. If you are unsure, ask in an issue first.

## Setup

```bash
# backend
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# frontend
cd ../frontend
npm ci
```

## Checks (CI runs the same)

```bash
cd backend
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
.venv/bin/pip-audit -r requirements.txt

cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build
npm audit --omit=dev
```

## Rules

- `backend/contracts/` is the source of truth for anything that crosses a module or
  process boundary. Change a contract first, then its implementations.
- Import boundaries: `core` never imports `workers`. `workers` import only `contracts`.
  `contracts` imports nothing first-party. A test enforces this.
- No file over about 400 lines (also tested).
- New dependencies must be MIT, BSD, or Apache-2.0 (or similar). Add each one to
  `THIRD_PARTY.md` in the same PR.
- No telemetry, no paid services. Claude is the only paid component.
- Record design changes in `docs/DECISIONS.md`.
