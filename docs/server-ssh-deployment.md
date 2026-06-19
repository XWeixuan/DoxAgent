# Server SSH Deployment Notes

## SSH Alias

Use the Windows SSH alias for all remote deployment and test commands:

```powershell
ssh doxagent-hk 'whoami && hostname'
```

`doxagent-hk` resolves to `root@43.135.22.202:22` through the local Clash SOCKS5
proxy at `127.0.0.1:7897`. In PowerShell, wrap remote Linux commands in single
quotes so expressions such as `$(whoami)` are evaluated on the server, not on
Windows.

## Server Layout

- DoxAgent deployment path: `/root/doxagent`
- Existing DoxAtlas path/container set must be left alone.
- DoxAtlas currently uses public ports `3000` and `8000`.
- DoxAgent Debug Viewer publishes only `127.0.0.1:8765:8765`, so it does not
  conflict with DoxAtlas and is not exposed directly to the public network.

## Deploy

From the local repo:

```powershell
git push origin main
ssh doxagent-hk 'cd /root/doxagent && git pull --ff-only && docker compose build debug-viewer && docker compose up -d debug-viewer'
```

The `.env` file is intentionally ignored by git. If the server needs real API or
Postgres settings, copy it over SSH instead of committing it:

```powershell
scp .env doxagent-hk:/root/doxagent/.env
```

## Verify

```powershell
ssh doxagent-hk 'cd /root/doxagent && docker compose ps && curl -fsS http://127.0.0.1:8765/api/config'
```

To inspect the viewer from Windows:

```powershell
ssh -N -L 8765:127.0.0.1:8765 doxagent-hk
```

Then open `http://127.0.0.1:8765`.

## Remote Eval Commands

Run tracked smoke/eval commands inside the DoxAgent image:

```powershell
ssh doxagent-hk 'cd /root/doxagent && docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 debug-viewer pytest -p no:cacheprovider -s tests/test_phase17_real_initialization_smoke.py::test_real_initialization_build_global_research_smoke'
ssh doxagent-hk 'cd /root/doxagent && docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 debug-viewer pytest -p no:cacheprovider -s tests/test_phase17_real_initialization_smoke.py::test_real_initialization_expectation_units_smoke'
```

Do not run broad `docker compose down` commands outside `/root/doxagent`; keep
DoxAgent and DoxAtlas compose projects isolated.
