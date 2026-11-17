"""Bound Reuters' analytics visit history without clearing authentication storage."""
import asyncio
import logging
import time
import weakref


# AB Tasty appends visits until the shared 5 MiB localStorage quota is exhausted.
# Arc's login SDK then reports a misleading host-connect error on token writes.
# Keep recent analytics history and every other field/key; no Storage override.
STORAGE_GUARD = r"""(() => {
  if (!['https://www.reuters.com', 'https://reuters.com'].includes(location.origin))
    return {state: 'OTHER_ORIGIN'};
  try {
    const raw = localStorage.getItem('ABTastyData');
    if (!raw || raw.length < 131072) return {state: 'SMALL'};
    const data = JSON.parse(raw);
    if (!Array.isArray(data.VisitedPages)) return {state: 'UNKNOWN_SCHEMA'};
    const before = data.VisitedPages.length;
    if (before <= 256) return {state: 'SMALL'};
    data.VisitedPages = data.VisitedPages.slice(-256);
    const trimmed = JSON.stringify(data);
    if (trimmed.length >= raw.length) return {state: 'UNCHANGED'};
    localStorage.setItem('ABTastyData', trimmed);
    return {state: 'TRIMMED', removed: before - 256,
            before_chars: raw.length, after_chars: trimmed.length};
  } catch (e) { return {state: 'UNAVAILABLE', error: e.name}; }
})()"""

_contexts = weakref.WeakKeyDictionary()


async def prepare_reuters_storage(page, *, force=False):
    """One init script/context; existing warm pages checked at most once/minute."""
    try:
        context = page.context
        now = time.monotonic()
        state = _contexts.setdefault(context, {'installed': False, 'checked': None})
        if not force and state['checked'] is not None and now - state['checked'] < 60:
            return
        state['checked'] = now
        async with asyncio.timeout(1):
            if not state['installed']:
                await context.add_init_script(STORAGE_GUARD)
                state['installed'] = True
            result = await page.evaluate(STORAGE_GUARD)
        if isinstance(result, dict) and result.get('state') == 'TRIMMED':
            logging.getLogger(__name__).info('Reuters analytics history trimmed removed=%s before_chars=%s after_chars=%s',
                result.get('removed'), result.get('before_chars'), result.get('after_chars'))
    except Exception:
        # Cache hygiene must not turn a disconnected/busy page into another
        # failed browser task. Normal runtime/control diagnostics still apply.
        return
