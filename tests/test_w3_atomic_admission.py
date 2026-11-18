import json
import pytest
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker
from doxagent.v2_control.repository import ControlRepository, ControlError
from tests.test_persistent_runtime_worker_receipts import _Worker, _request


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['pause', 'remove'])
async def test_existing_ticket_cannot_bypass_current_control_or_case_origin(tmp_path, change):
    journal = RuntimeJournal(tmp_path/'runtime.db')
    control = ControlRepository(journal)
    control.migrate()
    state = dict(analysis_allowed=True, epoch=1, removal_epoch=0, mode='MESSAGE_MONITORING')
    with journal.transaction() as db:
        db.execute('INSERT INTO v2_ticker_control VALUES(?,?,?)', ('MU', 1, json.dumps(state)))
    control.admit('case', 'MU')
    request = _request('gpt-6-luna', 'trade').model_copy(update={'idempotency_key': 'original', 'task_phase': 'trade_decision'})
    receipt = ReceiptWorker(_Worker(), journal, 'scope', case_id='case')
    await receipt._admit(request, None)
    if change == 'pause':
        state['analysis_allowed'] = False
    else:
        state['removal_epoch'] = 1
        state['epoch'] = 2
    with journal.transaction() as db:
        db.execute('UPDATE v2_ticker_control SET payload=? WHERE ticker=?', (json.dumps(state), 'MU'))
    with pytest.raises(ControlError, match='ANALYSIS_PAUSED' if change == 'pause' else 'ANALYSIS_REMOVED'):
        await receipt._admit(request, None)
