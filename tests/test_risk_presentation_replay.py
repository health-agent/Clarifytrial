"""Guard the scientific meaning of the saved teaching replay, not its styling."""
import importlib.util
import json
from pathlib import Path
import re

import pytest
from clarifytrial.interactive.paper_benchmark import PublicAcquisitionModel,ScalarRule

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def replay():
    text=(ROOT/'docs/internal/demo/clarifytrial-risk-replay.html').read_text(encoding='utf-8')
    return json.loads(re.search(r'<script id="replay-data" type="application/json">(.*?)</script>',text,re.S).group(1))


def test_teaching_cases_keep_frozen_aggregate_separate(replay):
    summary=json.loads((ROOT/'docs/internal/results/risk-aware-acquisition-v1/matched_test_summary.json').read_text(encoding='utf-8'))
    assert replay['summary']==summary
    assert [c['kind'] for c in replay['cases']]==['savings','verification','failure']
    assert '빈도' in replay['selection_note']
    assert replay['disclaimer'] and '수치 외' in replay['scope_question']


def test_replayed_actions_and_wrong_patients_match_revealed_truth(replay):
    for case in replay['cases']:
        for method in case['methods'].values():
            snapshots=method['snapshots'];initial=snapshots[0]['decisions'];final=snapshots[-1]['decisions']
            assert method['actions']==len(snapshots)-1
            assert method['visits']==sum(s['action']['route'] for s in snapshots[1:])
            assert method['correct']==sum(d['label']==gold for d,gold in zip(final,case['gold']))
            assert method['any_wrong']==any(a['label']<0 and b['label']>=0 and b['label']!=gold
                for a,b,gold in zip(initial,final,case['gold']))
            assert all(d['pending_question'] for d in final if d['label']<0)


def test_failure_case_really_has_two_agreeing_wrong_reports(replay):
    case=next(c for c in replay['cases'] if c['kind']=='failure')
    truth={v['name']:v['value'] for v in case['truth']}
    observations=case['methods']['risk_targeted']['snapshots'][-1]['observations']
    assert any(len(o['reports'])==2 and o['reports'][0]['value']==o['reports'][1]['value']!=truth[o['name']]
               for o in observations)
    assert case['methods']['risk_targeted']['any_wrong']


def test_export_refuses_a_saved_action_that_the_planner_did_not_choose():
    spec=importlib.util.spec_from_file_location('risk_replay_builder',ROOT/'scripts/build_risk_presentation_replay.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    model=PublicAcquisitionModel(('synthetic',),((0.,1.),),((.5,.5),),((ScalarRule(0,'gte',1.,False),),))
    row={'method':'risk_targeted','trace':'[[1,0,0]]'}
    with pytest.raises(AssertionError,match='no longer matches'):
        builder.method_replay(model,[{'nct_id':'NCT-SYNTHETIC'}],(-1,),(0,),{(0,0):0,(0,1):0},row)
