// Isolated PostgreSQL/WASM check. No network, server, Supabase credentials or data.
// Install the pinned test-only engine under .tmp as documented in the runbook.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PGlite } from '../.tmp/ticker-init-sql-check/node_modules/@electric-sql/pglite/dist/index.js';

const db = new PGlite();
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;');
  const migration = await readFile(new URL('../supabase/migrations/20260907061350_ticker_initialization_summaries.sql', import.meta.url), 'utf8');
  await db.exec(migration);
  await db.exec(migration); // Replay-safe DDL.
  const summary = {
    initialization_id: 'offline-init', ticker: 'MU', state_seq: 3, status: 'SUCCEEDED',
    phase: 'RUNTIME_START', created_at: '2026-09-05T00:00:00Z',
    updated_at: '2026-09-05T00:00:00Z', manual_resume_required: false, has_error: false,
  };
  await db.exec('SET ROLE service_role;');
  const upsert = (payload) => db.query('SELECT public.upsert_ticker_initialization_summary($1::jsonb)', [JSON.stringify(payload)]);
  await upsert(summary);
  await upsert({ ...summary, state_seq: 1, status: 'FAILED' });
  await upsert({ ...summary, status: 'FAILED' });
  const rows = (await db.query('SELECT state_seq, status FROM public.ticker_initialization_summaries')).rows;
  assert.deepEqual(rows, [{ state_seq: 3, status: 'SUCCEEDED' }]);
  await assert.rejects(upsert({ ...summary, state_seq: 4, status: 'PARTIAL' }));
  await db.exec('RESET ROLE;');
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`SET ROLE ${role};`);
    await assert.rejects(db.query('SELECT * FROM public.ticker_initialization_summaries'), /permission denied/);
    await assert.rejects(upsert(summary), /permission denied/);
    await db.exec('RESET ROLE;');
  }
  const flags = (await db.query("SELECT relrowsecurity FROM pg_class WHERE oid='public.ticker_initialization_summaries'::regclass")).rows;
  assert.equal(flags[0].relrowsecurity, true);
  const fn = (await db.query("SELECT prosecdef FROM pg_proc WHERE oid='public.upsert_ticker_initialization_summary(jsonb)'::regprocedure")).rows;
  assert.equal(fn[0].prosecdef, false);
  console.log('PASS: local PostgreSQL migration replay, monotonic RPC, role privileges, RLS, invoker');
} finally {
  await db.close();
}
