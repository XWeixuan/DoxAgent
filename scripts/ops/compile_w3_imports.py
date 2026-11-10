"""Build-time bytecode for the real Capsule import graph; never initialize SDK."""
import importlib.util
import json
from pathlib import Path
import py_compile
import sys


def compile_imports():
    from doxagent.codex_worker.schema import WorkerRunRequest  # noqa: F401
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime  # noqa: F401

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
    print(json.dumps({"compiled_sources": len(sources), "sdk_initialized": False}))


if __name__ == "__main__":
    compile_imports()
