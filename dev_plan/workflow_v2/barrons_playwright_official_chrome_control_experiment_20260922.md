# Barron's Playwright plus official Chrome 153 control experiment

## Confirmed predecessor result

The operator completed the first control using directly launched official Chrome
153 in Docker through the fixed US route. Barron's presented two challenges, both
were completed, login succeeded, and subscriber article content was readable.
This is the acceptance result; the earlier login-page and HTTP checks were not.

The successful direct-Chrome Profile remains mounted only in
`doxagent-barrons-chrome-control-profile` and is not reused by this experiment.

## Question

Can Playwright 1.63 launch the same official Google Chrome 153 binary and still
complete a real Barron's login and subscriber article read?

## Paired configuration

The second image derives from the successful direct-Chrome image, retaining:

- official Google Chrome 153 from Google's stable repository;
- Docker host, Xvfb 1440x1000, locale, timezone, UID 10001, Chrome sandbox,
  seccomp and AppArmor;
- fixed US proxy `doxagent-egress-clash:18081`;
- manual operation through loopback-only VNC.

The intentional changed variable is that Playwright 1.63 starts a persistent
context with `executable_path=/usr/bin/google-chrome-stable`. Playwright default
launch arguments and its transport are intentionally retained because they are
the subject of the test. No stealth library, UA/Client Hints override, request
interception, resource blocking, or script injection is added.

The experiment uses a new volume
`doxagent-barrons-playwright-chrome-control-profile` and VNC port 5902. It does
not read, copy, mount, or write the successful direct-Chrome Profile or any
governed Site Access Profile.

## Acceptance criterion

Success requires all of the following:

1. Complete every presented challenge.
2. Submit credentials and reach the authenticated account state.
3. Open a subscriber article and read its subscriber body.
4. Restart only this experiment container and retain the authenticated state.

Showing a login page, reaching a homepage, receiving HTTP 200, or merely setting
cookies is not acceptance.

## Interpretation

- Success means Playwright plus the official Chrome binary is not sufficient by
  itself to reproduce the governed-browser failure. Remaining differences must
  be narrowed to launch options, navigation/interception, Profile history, or
  the Site Access lifecycle.
- Failure, paired with the successful direct-Chrome control, strongly implicates
  Playwright's launch/transport/browser-visible automation surface. It still
  does not identify one specific default argument without a later ablation.
