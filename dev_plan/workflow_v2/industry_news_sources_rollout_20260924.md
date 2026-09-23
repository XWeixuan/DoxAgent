# AI hardware / semiconductor news sources — rollout record

## Scope

This change adds one ticker-scoped Barron's listing and eight shared distribution entrances: TrendForce News, TrendForce Press Releases, DIGITIMES English semiconductor More News, and the official HuggingNews, Tom's Hardware, The Elec, ETNews, and DIGITIMES Taiwan feeds. Shared entrances use the existing one-fetch / one-body / per-subscribed-ticker distribution pipeline and its existing realtime/closed-sweep windows. No default ticker subscription or monitoring definition is invented.

## Browser and egress decisions

| Entrance | Acquisition | Body owner | Access decision |
| --- | --- | --- | --- |
| Barron's ticker | Site Access crawler | Final publisher domain, not Barron's when an Other Dow Jones link is returned | Existing Dow Jones External Chrome identity; paid body needs the user's login |
| TrendForce News / Press | Site Access crawler | trendforce.com | Managed Playwright, server-direct |
| DIGITIMES English | Site Access crawler | digitimes.com | Managed Playwright, server-direct; paid body needs login |
| HuggingNews Atom | Official feed via German egress | huggingnews.com via same egress | Feed direct returned 403; German node returned 200 |
| Other four feeds | Official feed, direct | Their own publisher domain | Feed direct returned 200 |

DIGITIMES English was tested with temporary Profiles on the same German exit using both Managed Playwright and ordinary Chrome + CDP; both were denied. The same page was readable with Managed Playwright over server-direct, so changing browser mode would not address that test result. Barron's Managed-vs-External choice is based on the earlier controlled login experiment; the current ticker listing could not be fetched while the existing US/JP exits were unavailable.

## Verification and limits

- Live listing/feed parsing: TrendForce News 7, Press 5, DIGITIMES More News 11, Tom's Hardware 50, The Elec 20, ETNews 50, DIGITIMES Taiwan 40; HuggingNews feed was 200 through the German exit. Counts are single-snapshot parser checks, **not** freshness or production-delivery coverage.
- Public sample body extraction was checked for the above sites, including a Korean ETNews short-full article. DIGITIMES English returned only a public teaser; Barron's and DIGITIMES paid full-body access remain contingent on separate manual login and article verification.
- Site Strategy seeds bind public sites to the verified direct/German exits and leave existing Dow Jones Profile/egress bindings intact. The UK/US/JP egress labels are not silently mapped to another region.
- `barrons_ticker_news` is not added to the global default ticker profile. The eight shared sources likewise require an explicit ticker subscription and monitoring terms in `en`, `ko`, and `zh-Hant` before business delivery. This avoids surprise fan-out, Jev spend, or fabricated ticker definitions.
- On production, check Site Access strategy/identity readiness, message-source registration, live polling, body job outcome, and final ticker publication separately. A 200 listing alone is not end-to-end acceptance.
