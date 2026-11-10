"""Run the Codex worker as a standalone service."""

import logging

import uvicorn


def main() -> None:
    # Uvicorn configures only its own loggers. Provide the normal root sink for
    # the explicitly enabled, bounded W3 timing logger; all other app logs keep
    # their WARNING default rather than enabling global INFO traffic.
    logging.basicConfig(level=logging.WARNING)
    uvicorn.run(
        "doxagent.codex_worker.app:app_from_environment",
        factory=True,
        host="0.0.0.0",
        port=8791,
        proxy_headers=False,
        workers=1,
        reload=False,
    )


if __name__ == "__main__":
    main()
