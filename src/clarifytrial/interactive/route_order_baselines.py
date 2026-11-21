"""Simple route-order comparators fixed before the November calibration run.

These reuse the same beliefs, observed-witness decoder and risk threshold.
They change acquisition only; no patient truth or future report is available.
"""
from .risk_aware_acquisition import RiskAwareAcquisition, UNTRIED


VISIT_PENALTIES = {
    "risk_myopic_cost_005": .05,
    "risk_myopic_cost_010": .10,
    "risk_myopic_cost_025": .25,
}
ROUTE_METHODS = ("risk_record_first", *VISIT_PENALTIES)


class RecordFirstAcquisition(RiskAwareAcquisition):
    """Use the myopic fact choice, but try that fact's record before a visit.

    After failure or an insufficient report the usual myopic selector runs
    again, so it may visit/recheck that fact or choose another fact. A record
    already attempted is never retried. This does not prohibit all visits
    until all records have been exhausted.
    """

    def choose(self, fixed, state, visits, remaining, *, policy="targeted", horizon=1):
        action = super().choose(fixed, state, visits, remaining, policy=policy, horizon=1)
        if action is None:
            return None
        fact, route = action
        if route == 1 and state[2*fact] == UNTRIED:
            return fact, 0
        return action
