"""Build-time bytecode for the real Capsule import graph; never initialize SDK."""
import importlib.util
import json
from pathlib import Path
import py_compile
import sys


def compile_imports(*, include_calendar=False):
    from doxagent.codex_worker.schema import WorkerRunRequest  # noqa: F401
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime  # noqa: F401
    if include_calendar:
        # Compile the observed pandas/calendar cold path without constructing
        # a schedule or initializing an SDK client.
        import exchange_calendars  # noqa: F401

    sources = sorted({
        str(module.__file__) for module in tuple(sys.modules.values())
        if isinstance(getattr(module, "__file__", None), str)
        and module.__file__.endswith(".py")
        and Path(module.__file__).is_file()
    })
    for source in sources:
        target = py_compile.compile(
            source, doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        )
        header = Path(target).read_bytes()[:16]
        if header[8:16] != importlib.util.source_hash(Path(source).read_bytes()):
            raise RuntimeError("compiled source hash does not match: " + source)
    print(json.dumps({"compiled_sources": len(sources), "sdk_initialized": False,
                      "calendar_imported": include_calendar}))


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--calendar',action='store_true')
    compile_imports(include_calendar=parser.parse_args().calendar)
