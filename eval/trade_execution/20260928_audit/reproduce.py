"""Offline characterization of observed defects; never connects to IBKR."""
import asyncio
import json
import tempfile
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.test_trade_execution import admit, run_job, setup


def scenario(path):
    path.mkdir()
    return setup.__wrapped__(path)


def main():
    evidence = {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        clock, repo, revision, executor, broker = scenario(root / 'overlap')
        admit(repo, revision, 'trade:A')
        asyncio.run(run_job(executor, repo, clock, 'trade:A:entry'))
        admit(repo, revision, 'trade:B')
        asyncio.run(run_job(executor, repo, clock, 'trade:B:entry'))
        quantities = [repo.require('lots', identity)['remaining_qty'] for identity in ('trade:A', 'trade:B')]
        assert quantities == [198, 198]
        evidence['overlap'] = {'owned_lot_quantities': quantities, 'position_including_manual_50': broker.positions[9939]}

        clock, repo, revision, executor, broker = scenario(root / 'loss')
        admit(repo, revision, 'trade:A')
        asyncio.run(run_job(executor, repo, clock, 'trade:A:entry'))
        clock.value = datetime.fromisoformat(repo.require('lots', 'trade:A')['scheduled_exit'])
        broker.quote_price = 80
        asyncio.run(run_job(executor, repo, clock, 'trade:A:exit'))
        admit(repo, revision, 'trade:B')
        asyncio.run(run_job(executor, repo, clock, 'trade:B:entry'))
        assert broker.sent[-1]['quantity'] == 247
        evidence['loss_does_not_change_budget'] = {'gross_loss_usd': 3960, 'next_entry_quantity': 247, 'next_entry_limit': broker.sent[-1]['limit_price'], 'configured_target_usd': str(repo.profile(revision).strategy.target_notional_usd)}

        clock, repo, revision, executor, broker = scenario(root / 'rejection')
        def reject(attempt):
            broker.sent.append(attempt)
            broker.next_id = attempt['order_id'] + 1
            broker.order_event(attempt, 'Inactive')
            broker.sink('order', {'order_id': attempt['order_id'], 'client_id': attempt['client_id'], 'status': 'Rejected', 'error': {'code': 201, 'message': 'Insufficient margin'}})
            broker.order_event(attempt, 'Cancelled')
        broker.submit = reject
        admit(repo, revision, 'trade:A')
        asyncio.run(run_job(executor, repo, clock, 'trade:A:entry'))
        assert [a['order_type'] for a in broker.sent] == ['LMT', 'MKT']
        assert repo.require('executions', 'trade:A')['entry_reason'] == 'RETRY_BUDGET_EXHAUSTED'
        evidence['rejected_then_cancelled'] = {'sent_order_types': [a['order_type'] for a in broker.sent], 'result_reason': repo.require('executions', 'trade:A')['entry_reason'], 'persisted_broker_errors': [a.get('broker_error') for a in repo.attempts('trade:A:entry')]}
    output = Path(__file__).with_name('reproductions.json')
    output.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
