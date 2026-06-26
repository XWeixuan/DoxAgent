@echo off
setlocal
set "UV_CACHE_DIR=%CD%\.uv-cache"
uv run python -m doxagent.stocktwits.cli %*
