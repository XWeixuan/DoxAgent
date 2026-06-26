param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CrawlerArgs
)

$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv run python -m doxagent.stocktwits.cli @CrawlerArgs
