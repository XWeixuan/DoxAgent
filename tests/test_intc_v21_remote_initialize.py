from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts.ops.intc_v21_remote_initialize import GatedDocument2Orchestrator, O0Boundary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,stop,status",
    [
        (2, False, "shell_count_rejected"),
        (7, False, "shell_count_rejected"),
        (3, True, "awaiting_shell_count_release"),
        (6, True, "awaiting_shell_count_release"),
        (4, False, None),
    ],
)
async def test_o0_gate_preserves_result_and_blocks_before_o1(count, stop, status):
    runner = object.__new__(GatedDocument2Orchestrator)
    runner.stop_after_o0 = stop
    result = (SimpleNamespace(shells=list(range(count))), {})
    with patch(
        "scripts.ops.intc_v21_remote_initialize.CodexDocument2Orchestrator._construct_shells",
        new=AsyncMock(return_value=result),
    ):
        if status:
            with pytest.raises(O0Boundary) as error:
                await runner._construct_shells()
            assert (error.value.count, error.value.status) == (count, status)
        else:
            assert await runner._construct_shells() is result
