# Barron's official Chrome 153 control experiment

## Purpose

Test whether a manually operated, official Google Chrome 153 browser in the same
Docker host and through the same US proxy can complete a real Barron's login.
Showing a login page or returning HTTP 200 is not a successful result.

## Controlled configuration

- Browser: official `google-chrome-stable` 153 from Google's Debian repository.
- Launch: direct Chrome process; no Playwright, CDP, automation extension,
  headless mode, UA override, Client Hints override, or resource interception.
- Runtime: non-root UID 10001, Chrome sandbox retained, existing Site Access
  seccomp/AppArmor policy, Xvfb 1440x1000, `en_US.UTF-8`,
  `America/Los_Angeles`.
- Route: the existing fixed US slot `doxagent-egress-clash:18081`.
- State: dedicated `doxagent-barrons-chrome-control-profile` volume. It never
  mounts or reads a governed Site Access Profile.
- UI: VNC is exposed only at host loopback `127.0.0.1:5901`.

## Acceptance criterion

Success requires all of the following manual observations:

1. Complete any challenge presented by Barron's.
2. Submit valid credentials and reach an authenticated account state.
3. Open a known subscriber article and read the subscriber body.
4. Restart only the control container and confirm the authenticated state is
   retained by the isolated Profile.

Challenge display, login-page display, homepage HTTP 200, or cookie creation
alone are not acceptance.

## Interpretation boundary

- If this control succeeds while the governed browser fails through the same
  route, the strongest remaining difference is the CfT/Playwright launch and
  browser identity stack.
- If this control also fails, that result does not by itself prove Docker is the
  cause. The US exit IP reputation remains a confounder because the xRDP Chrome
  baseline may use a different route. The next strict control would be xRDP
  official Chrome through this same US slot, or this Docker control through the
  same route used by the successful xRDP browser.

The experiment must not close or alter any active Site Access login-maintenance
session.
