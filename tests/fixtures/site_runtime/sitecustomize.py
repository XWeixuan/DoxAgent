"""Isolated smoke-only traceback hook. Never installed in an application image."""

import traceback

if "/tmp/doxagent-source-runtime-code-" in __file__:
    from doxagent.site_strategy.runtime import SiteAccessRuntime

    _recover = SiteAccessRuntime.recover_login

    async def diagnostic_recover(self, *args, **kwargs):
        try:
            return await _recover(self, *args, **kwargs)
        except Exception:
            traceback.print_exc()
            raise

    SiteAccessRuntime.recover_login = diagnostic_recover
