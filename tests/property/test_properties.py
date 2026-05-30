"""Property-style invariant tests."""

from pke.identity.batch_cluster import adjusted_rand_index
from pke.mastery.state import delta_for


def test_identity_stability_under_identical_labels():
    assert adjusted_rand_index([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0


def test_mastery_reinforcement_delta_is_non_negative():
    assert delta_for(grade="pass", grader_kind="symbolic", item_type="replay_self_try") > 0
    assert delta_for(grade="fail", grader_kind="symbolic", item_type="variant") < 0
