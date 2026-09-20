"""Trusted Reuters browser recipe shared by legacy and Site Access runtimes."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote


async def capture_reuters_search(page: Any, query: str, offset: int) -> list[dict[str, object]]:
    response = await page.goto(
        f"https://www.reuters.com/site-search/?query={quote(query)}&offset={offset}",
        wait_until="domcontentloaded",
    )
    status = response.status if response is not None else 200
    if status >= 400:
        error = RuntimeError(f"Reuters search returned HTTP {status}")
        error.status_code = status
        raise error
    await page.wait_for_function(
        r"""() => {
          const body = document.body?.innerText || '';
          const articlePath = /\/[^/]+\/[^/]+-\d{4}-\d{2}-\d{2}\//;
          const hasArticle = [...document.querySelectorAll('main a[href]')]
            .some(link => articlePath.test(link.getAttribute('href') || ''));
          return hasArticle || /Search results for[\s\S]*?\b0 results\b/i.test(body);
        }""",
        timeout=12_000,
    )
    rows = await page.evaluate(
        r"""() => {
          const months = '(?:January|February|March|April|May|June|July|August|'
            + 'September|October|November|December)';
          const pattern = new RegExp(months + '\\s+\\d{1,2},\\s+\\d{4}');
          const out = [], seen = new Set();
          for (const link of document.querySelectorAll('main a[href]')) {
            const href = link.getAttribute('href') || '';
            const title = (link.textContent || '').trim();
            const articlePath = /\/[^/]+\/[^/]+-\d{4}-\d{2}-\d{2}\//;
            if (!title || title.length < 15 || !href.startsWith('/')
                || !articlePath.test(href) || seen.has(href)) continue;
            let node = link, card = null;
            for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
              if (node.tagName === 'MAIN' || node.tagName === 'BODY') break;
              const articles = new Set([...node.querySelectorAll('a[href]')]
                .map(a => a.getAttribute('href')).filter(h => articlePath.test(h || '')));
              if (articles.size > 1) break;
              if (articles.size === 1) card = node;
              if (card && node.matches('article, li, [data-testid*="card"]')) break;
            }
            const text = (card?.innerText || card?.textContent || '').replace(/\s+/g, ' ').trim();
            const match = text.match(pattern);
            const urlDate = href.match(/-(\d{4}-\d{2}-\d{2})\/$/);
            const publishedDate = urlDate?.[1] || match?.[0];
            if (!publishedDate) continue;
            seen.add(href);
            const summaryNode = card?.querySelector(
              '[data-testid*="description"], [data-testid*="summary"], p');
            let summary = (summaryNode?.textContent || '').trim();
            const relativePattern = /\b\d+\s+(?:mins?|minutes?|hours?)\s+ago\b/i;
            if (summary === title || (summary.length < 80
                && (relativePattern.test(summary) || pattern.test(summary)))) summary = '';
            const relative = text.match(relativePattern)?.[0];
            out.push({url: href, title, date: publishedDate, summary,
              date_basis: urlDate ? 'url_date' : 'card_date',
              card_date: match?.[0] || null, relative_time: relative || null});
          }
          return out;
        }"""
    )
    return cast(list[dict[str, object]], rows)


__all__ = ["capture_reuters_search"]
