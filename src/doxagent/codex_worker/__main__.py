"""Run the Codex worker as a standalone service."""

import uvicorn


def main() -> None:
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
