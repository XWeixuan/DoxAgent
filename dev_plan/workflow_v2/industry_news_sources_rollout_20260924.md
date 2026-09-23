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

## Production acceptance (2026-09-24)

- Pushed `2fd67922` to `main`, pulled it on the Singapore host, built `doxagent-v2:server` and `doxagent-site-access:server`, and recreated only Site Access, Message Bus, and Content Enrichment. Chrome Supervisor retained container ID prefix `5aa49999899b` and stayed healthy; its existing Profile volume was not recreated. Site Strategy SQLite was backed up online to `/site-data/backups/pre-industry-sources-20260924.sqlite3` and passed `integrity_check`.
- The first Site Access recreation exposed six legacy challenge fields in production BrowserProfile JSON that its strict model did not accept. The follow-up compatibility change round-trips those fields without deleting or rewriting Profile data. After the rebuilt image was applied, Site Access returned healthy.
- Registry shows all eight site policies, including the Barron's crawler ref, and all nine enabled Message Bus source definitions. Existing `dowjones-main` remains on `us-standard-5` with `VALID` auth status; its session was not copied to a sibling Profile. No new source has a ticker binding, and production currently has zero submitted monitoring-term configurations, so **ticker publication from these sources has not been accepted**.
- Actual Site Access browser-listing probes: TrendForce News `200 / 7 rows`, TrendForce Press `200 / 5 rows`, DIGITIMES English More News `200 / 11 rows`. Actual feed probes: HuggingNews `200 / 45`, Tom's Hardware `200 / 50`, The Elec `200 / 20`, ETNews `200 / 50`, DIGITIMES Taiwan `200 / 40`.
- Actual `body_v2.2` sample extraction: TrendForce News 4,743 characters, Press 4,885, HuggingNews 902, Tom's Hardware 3,150, The Elec 1,141, ETNews 593, DIGITIMES Taiwan 1,511; all succeeded. DIGITIMES English returned `no_authenticated_profile_available`, as expected before its login is maintained. Barron's ticker listing and paid body were not accepted because both existing `us-standard-5` and `jp-standard-6` egresses are `UNAVAILABLE` (86/91 consecutive probe failures at inspection); `server-direct` and `de-standard-1` are `READY`. Egress binding was not silently changed.
- Next business activation requires user-approved monitoring terms in all three current languages (`en`, `ko`, `zh-Hant`) and explicit ticker subscriptions. For Barron's, first restore a compatible US/JP exit (or deliberately provision a new identity/exit), then validate the ticker page and authenticated article on the same long-lived Profile. DIGITIMES English needs a manual login and verification article check.
