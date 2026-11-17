"""Reproduce existing clock-sensitive failures with the original Policy helper."""
import ast,subprocess,sys
import pytest
from doxagent.persistent_runtime_v2 import service
source=subprocess.check_output(['git','show','HEAD:src/doxagent/persistent_runtime_v2/service.py'],text=True,encoding='utf8')
node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_w2_detail_business_payload')
exec(compile(ast.Module(body=[node],type_ignores=[]),'<HEAD Policy helper>','exec'),vars(service))
sys.exit(pytest.main(['-q','--tb=line','--show-capture=no',
 'tests/test_runtime_policy_direction_usage.py::test_native_r1_r2_w3_ready_direction_and_delta_survive_rejection',
 'tests/test_runtime_policy_direction_usage.py::test_output_guard_head_race_is_retryable_and_stale_generation_rejected',
 'tests/test_runtime_policy_direction_usage.py::test_completed_w3_result_retries_output_without_research_again',
 '--junitxml=eval/runtime_cache_cost_repair_20261117/preexisting-failures.xml']))
