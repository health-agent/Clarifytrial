from clarifytrial.interactive.paper_benchmark import PublicAcquisitionModel, ScalarRule
from clarifytrial.interactive.risk_aware_acquisition import RiskAwareAcquisition
from clarifytrial.interactive.route_order_baselines import RecordFirstAcquisition


def simple_model():
    return PublicAcquisitionModel(("synthetic_fact",), ((0., 1.),), ((.5, .5),),
                                  ((ScalarRule(0, "gte", 1., False),),))


def test_record_first_changes_first_route_without_changing_decoder():
    common=dict(coupling=(0.,),risk_cutoff=.03)
    myopic=RiskAwareAcquisition(simple_model(),**common)
    record=RecordFirstAcquisition(simple_model(),**common)
    assert myopic.choose((-1,),(-2,-2),1,3,horizon=1)==(0,1)
    assert record.choose((-1,),(-2,-2),1,3)==(0,0)
    for state in ((-2,-2),(-1,-2),(0,-2),(0,0),(0,1)):
        assert record.decisions((-1,),state)==myopic.decisions((-1,),state)


def test_record_first_does_not_retry_missing_or_insufficient_record():
    planner=RecordFirstAcquisition(simple_model(),coupling=(0.,),risk_cutoff=.03)
    assert planner.choose((-1,),(-1,-2),1,2)==(0,1)
    assert planner.choose((-1,),(0,-2),1,2)==(0,1)
    assert planner.choose((-1,),(0,0),0,1) is None
    assert planner.choose((-1,),(-1,-2),0,2) is None
    assert planner.choose((-1,),(-2,-2),1,0) is None


def test_visit_penalty_can_change_one_step_route():
    model=simple_model()
    no_cost=RiskAwareAcquisition(model,coupling=(0.,),risk_cutoff=.1)
    costly=RiskAwareAcquisition(model,coupling=(0.,),risk_cutoff=.1,visit_penalty=.3)
    assert no_cost.choose((-1,),(-2,-2),1,1,horizon=1)==(0,1)
    assert costly.choose((-1,),(-2,-2),1,1,horizon=1)==(0,0)
