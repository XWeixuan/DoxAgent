# Barron's ordinary Chrome plus CDP attach experiment

## Prior paired results

1. Directly launched official Chrome 153 in Docker: two challenges completed,
   login succeeded, subscriber article body readable.
2. Playwright 1.63 `launch_persistent_context` with the same official Chrome,
   Docker host and US exit: the first challenge completed, then Barron's showed
   `Access is temporarily restricted`; login failed.

These are real login outcomes, not login-page or HTTP reachability results.

## Question

Does Barron's still allow login when Chrome is started as an ordinary standalone
process and Playwright attaches afterward over CDP to navigate and control it?

## Controlled configuration

- The image derives from the same official Chrome 153 and Playwright 1.63 pair.
- The entrypoint, not Playwright, starts `google-chrome-stable`.
- Chrome receives only the successful direct-control arguments plus an internal
  `--remote-debugging-address=127.0.0.1` and fixed port 9222. Port 9222 is not
  published by Docker.
- After Chrome reports its CDP endpoint ready, Playwright calls
  `connect_over_cdp`, obtains the existing default context and navigates its
  initial page to the Barron's login URL.
- There is no Playwright launch call, stealth library, UA/Client Hints override,
  request interception, resource blocking, script injection, headless mode,
  `--enable-automation`, or `--no-sandbox`.
- Docker host, Xvfb, 1440x1000 window, locale, timezone, UID 10001, sandbox,
  seccomp/AppArmor and US egress slot 18081 remain paired with earlier controls.
- The new Profile volume and loopback VNC port 5903 are isolated from both prior
  experiment Profiles and every governed Site Access Profile.

## Acceptance criterion

Success requires completing all challenges, reaching an authenticated account,
reading a subscriber article body, and subsequently retaining authentication
after only this experiment container is restarted.

## Interpretation boundary

- If CDP attach succeeds, Playwright control alone is not sufficient to trigger
  the failure. The leading cause moves to Playwright's browser launch arguments
  or launch path.
- If CDP attach fails like `launch_persistent_context`, CDP exposure or
  Playwright-driven page behavior remains a plausible trigger. A later ablation
  would still be needed to distinguish CDP-listening Chrome without attachment
  from actual Playwright attachment/control.

## Remote preflight (2026-09-22)

- Deployed revision: `dcd3d952`.
- Browser/runtime: official Google Chrome `153.0.8010.52`, Playwright `1.63.0`.
- The observed main Chrome command contained only the direct-control arguments
  plus `--remote-debugging-address=127.0.0.1`,
  `--remote-debugging-port=9222`, and initial `about:blank`. It did not contain
  Playwright launch defaults, `--enable-automation`, `--no-sandbox`, headless or
  identity override arguments.
- The internal CDP target showed Playwright navigation had reached the Dow Jones
  SSO authorization page and its challenge iframe. Host port inspection exposed
  only VNC at `127.0.0.1:5903`; CDP port 9222 was not published.
- Browser process: UID/GID 10001, `NoNewPrivs=1`, seccomp mode 2, AppArmor
  `doxagent-site-access (enforce)`.
- Route probe: fixed US slot 18081 returned `38.181.82.188`.
- New experiment, successful direct-Chrome control, failed Playwright-launch
  control, and production Site Access containers were all healthy with restart
  count 0.

Manual challenge, credential, entitlement, and restart-persistence acceptance
is pending operator interaction.

## Operator and persistence result (2026-09-22)

The operator completed two challenges, logged in successfully and read Barron's
article content. The experiment container was then restarted with a 45-second
grace period. Its start timestamp changed from `2026-09-22T05:36:25Z` to
`2026-09-22T07:14:59Z`, while the dedicated Cookie and Preferences files
remained present with UID/GID 10001 and mode 0600.

After restart:

- the configured login navigation resolved to `https://www.barrons.com/`
  instead of Dow Jones SSO or a restriction page;
- only authentication-cookie names and presence were inspected, never values;
  `sso`, `session`, `connect.sid`, `djcs_route`, and `datadome` markers remained;
- the Registry verification article returned HTTP 200 at its expected Barron's
  path, with no challenge or temporary-restriction marker;
- the rendered `article` element contained 45 paragraphs and 8,145 characters.

The CDP-attach Profile therefore retained a usable authenticated Barron's
session across container restart. The result supports the hypothesis that the
failed paired control is caused by Playwright's browser-launch path/default
launch environment rather than Playwright control over an already normally
started Chrome process.
