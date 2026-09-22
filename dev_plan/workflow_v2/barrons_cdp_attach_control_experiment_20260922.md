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
