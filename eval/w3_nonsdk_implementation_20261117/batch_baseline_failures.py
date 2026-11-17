import __future__
from pathlib import Path
import pytest
from doxagent.event_library import repository as module
scope=dict(vars(module))
exec(compile(Path(__file__).with_name('legacy_published_events.py').read_text(),'<legacy>','exec',flags=__future__.annotations.compiler_flag),scope)
module.EventLibraryRepository.published_events=scope['published_events']
raise SystemExit(pytest.main([
 'tests/test_event_library_foundation.py::test_workflow_runner_uses_explicit_ids_and_noops_without_eligible_documents',
 'tests/test_event_library_incremental_consumers.py::test_document2_injects_full_reference_payload_once_per_shell_thread',
 'tests/test_event_library_time_reference_repair.py::test_broad_event_same_fact_and_candidate_priority_are_hard_errors',
 'tests/test_event_library_time_reference_repair.py::test_time_error_codes_are_stable_for_missing_future_and_subject_leak',
 'tests/test_event_library_time_reference_repair.py::test_supersession_requires_both_sides_and_normalizes_target_reason',
 '-q','--junitxml=eval/w3_nonsdk_implementation_20261117/batch-baseline-failures.xml']))
