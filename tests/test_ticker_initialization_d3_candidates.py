from concurrent.futures import ThreadPoolExecutor

from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
from tests.test_codex_document3_workflow import _policy_set


def test_candidate_publication_does_not_move_live_head_and_normal_publish_skips_reserved(tmp_path):
    repo = SQLiteDocument3PolicyRepository(tmp_path / "d3.db")
    original = _policy_set()
    repo.publish(original, expected_base_version=None)
    version = repo.reserve_version(original.ticker, "candidate")
    candidate = _policy_set(version)
    repo.publish_candidate(candidate, run_id="candidate")
    repo.publish_candidate(candidate, run_id="candidate")
    assert repo.get_current(original.ticker) == original
    assert repo.reserved_policy(original.ticker, "candidate") == candidate
    following = repo.reserve_version(original.ticker, "normal")
    assert following == version + 1
    repo.publish(_policy_set(following), expected_base_version=1)
    assert repo.get_current_version(original.ticker) == following
    assert repo.get_version(original.ticker, version) == candidate


def test_version_reservation_is_stable_and_unique_across_connections(tmp_path):
    path = tmp_path / "d3.db"
    SQLiteDocument3PolicyRepository(path)

    def reserve(identity):
        return SQLiteDocument3PolicyRepository(path).reserve_version("MU", identity)

    with ThreadPoolExecutor(max_workers=3) as pool:
        versions = list(pool.map(reserve, ["a", "b", "c", "a", "b", "c"]))
    assert versions[:3] == versions[3:]
    assert len(set(versions)) == 3
