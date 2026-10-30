"""`python -I host.py <folder>`: runs one ability in its own process (standard library only).

Arguments arrive as JSON on stdin; the last line printed is {"ok": …, "output": …}. What the ability
itself prints goes to stderr, so it can't forge that line."""

import contextlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path


def main() -> None:
    folder = Path(sys.argv[1])
    sys.path[:0] = [str(folder / "lib"), str(folder)]
    out = sys.stdout
    sys.stdout = sys.stderr
    try:
        args = json.loads(sys.stdin.read() or "{}")
        spec = importlib.util.spec_from_file_location("ability_main", folder / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = {"ok": True, "output": str(module.run(args))}
    except BaseException as exc:
        lines = traceback.format_exception(exc)
        result = {"ok": False, "output": "".join(lines[-6:]).strip()}
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    out.write("\n" + json.dumps(result) + "\n")
    out.flush()


if __name__ == "__main__":
    main()
