import pytest

from stale_engine import state
from stale_engine.state import State, derive


@pytest.mark.parametrize("labels,fire,expected", [
    (["stale-claim::a", "stale-state::quarantined"], False, State.QUARANTINED),
    (["stale-state::quarantined", "stale-state::reproving"], False, State.QUARANTINED),
    (["stale-state::reproving"], True, State.REPROVING),
    (["stale-state::remediating"], False, State.REMEDIATING),
    (["stale-state::retry-pending"], True, State.REMEDIATING),
    (["stale-state::stale"], False, State.STALE),
    (None, False, State.VALID),
    (None, True, State.STALE),
    ([], True, State.STALE),       # open issue without a state label falls through to rules
    ([], False, State.VALID),
])
def test_derivation_table(labels, fire, expected):
    assert derive(labels, fire) is expected


def test_transition_leaves_one_state_label():
    t = state.transition("reproving")
    assert t["add_labels"] == ["stale-state::reproving"]
    assert set(t["remove_labels"]) == {"stale-state::stale", "stale-state::remediating",
                                       "stale-state::retry-pending", "stale-state::quarantined"}


def test_unknown_state_label():
    with pytest.raises(ValueError):
        state.state_label("bogus")


def test_label_helpers():
    labels = ["stale-claim::anchor-trusted", "stale-attempt::1", "stale-attempt::2", "other"]
    assert state.claim_of(labels) == ["anchor-trusted"]
    assert state.attempt(labels) == 2
    assert state.attempt([]) == 0
    assert state.claim_label("x") == "stale-claim::x"
