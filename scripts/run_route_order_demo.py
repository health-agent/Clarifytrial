"""Small offline synthetic example; it is not an evaluation or clinical input."""
from __future__ import annotations
import argparse
import json

from clarifytrial.interactive.paper_benchmark import PublicAcquisitionModel, ScalarRule
from clarifytrial.interactive.risk_aware_acquisition import RiskAwareAcquisition
from clarifytrial.interactive.route_order_baselines import RecordFirstAcquisition
from clarifytrial.disclaimer import DEFAULT_MEDICAL_DISCLAIMER


def run_demo(record_missing=False):
    model=PublicAcquisitionModel(("synthetic_score",),((0.,1.),),((.5,.5),),
        ((ScalarRule(0,"gte",1.,False),),))
    results=[]
    # Reports are held by the simulator, never passed to the chooser in advance.
    reports={0:-1 if record_missing else 0,1:0}
    for name,planner,horizon in (
        ("one_step",RiskAwareAcquisition(model,coupling=(0.,)),1),
        ("record_first",RecordFirstAcquisition(model,coupling=(0.,)),1),
        ("two_step",RiskAwareAcquisition(model,coupling=(0.,)),2)):
        state=planner.empty_state();visits=0;steps=[]
        for turn in range(3):
            action=planner.choose((-1,),state,1-visits,3-turn,horizon=horizon)
            if action is None:break
            fact,route=action;report=reports[route];visits+=route
            state=state[:2*fact+route]+(report,)+state[2*fact+route+1:]
            decision,=planner.decisions((-1,),state)
            steps.append({"source":"existing_record" if route==0 else "visit",
                "report":None if report<0 else model.domains[fact][report],
                "status":{-1:"pending",0:"subset_violated",1:"subset_supported"}[decision.label],
                "model_error_score":round(decision.model_error_upper,6)})
        decision,=planner.decisions((-1,),state)
        results.append({"method":name,"steps":steps,"visits":visits,
            "pending_question":"Obtain sufficient observed evidence for synthetic_score." if decision.label<0 else None})
        planner.clear_caches()
    return {"input":"Synthetic score >= 1; reports are generated as 0. No real patient or LLM call.",
        "record_missing":record_missing,"methods":results,"medical_disclaimer":DEFAULT_MEDICAL_DISCLAIMER}


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument('--record-missing',action='store_true');args=parser.parse_args()
    print(json.dumps(run_demo(args.record_missing),ensure_ascii=False,indent=2))
