"""Reconstruct three teaching cases from frozen, paired synthetic test traces.

Cases are deliberately selected after aggregate evaluation for explanation, not
for estimating an effect. Every action is checked against the actual planner.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random

from clarifytrial.disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from clarifytrial.interactive.paper_benchmark import make_model,generate_patients,reference_statuses
from clarifytrial.interactive.risk_aware_acquisition import RiskAwareAcquisition

NAMES={'alt_xuln':'간 수치 ALT','ast_xuln':'간 수치 AST','body_mass_index_kg_m2':'체질량지수 BMI',
       'body_weight_kg':'체중','egfr_ml_min':'신장 기능 eGFR','egfr_ml_min_1_73m2':'보정 신장 기능 eGFR',
       'hba1c':'당화혈색소 HbA1c','hemoglobin_g_dl':'혈색소','platelet_count_10_9_l':'혈소판 수',
       'ecog_score':'활동도 ECOG','absolute_neutrophil_count_10_9_l':'호중구 수',
       'serum_creatinine_mg_dl':'크레아티닌','heart_rate_bpm':'심박수'}
UNITS={'alt_xuln':'정상 상한 배수','ast_xuln':'정상 상한 배수','body_mass_index_kg_m2':'kg/m²',
       'body_weight_kg':'kg','egfr_ml_min':'mL/min','egfr_ml_min_1_73m2':'mL/min/1.73m²',
       'hba1c':'%','hemoglobin_g_dl':'g/dL','platelet_count_10_9_l':'10⁹/L','ecog_score':'점',
       'absolute_neutrophil_count_10_9_l':'10⁹/L','serum_creatinine_mg_dl':'mg/dL','heart_rate_bpm':'회/분'}
CASE_NAMES={'savings':'방문 없이 같은 판정을 끝낸 사례','verification':'재확인으로 보류를 줄인 사례','failure':'두 보고가 같아도 오판한 사례'}


def choose_cases(rows):
    pairs=defaultdict(dict)
    for row in rows:pairs[row['patient']][row['method']]=row
    selected={}
    for kind in CASE_NAMES:
        for patient,pair in sorted(pairs.items(),key=lambda p:int(p[0].rsplit(':',1)[1])):
            a,b=pair['risk_targeted'],pair['risk_myopic'];trace=json.loads(a['trace'])
            repeated=any(f==ff and route!=rr for f,route,_ in trace for ff,rr,_ in trace)
            if kind=='savings':ok=int(a['visits'])<int(b['visits']) and int(a['correct'])>=int(b['correct']) and not int(a['any_wrong'])
            elif kind=='verification':ok=repeated and int(a['correct'])>int(b['correct']) and not int(a['any_wrong'])
            else:ok=repeated and int(a['any_wrong']) and float(a['coupling'])==1
            if ok:selected[kind]=(patient,pair);break
        if kind not in selected:raise ValueError(f'No teaching case: {kind}')
    return selected


def reconstruct(data,indices,seed=56002,episodes=1200):
    rng=random.Random(seed);models=[make_model(g,'heldout') for g in data['groups']]
    found={}
    for i in range(episodes):
        group=rng.randrange(len(models));m=models[group];mask=rng.randrange(2);rho=rng.choice((0.,.5,1.))
        truth=generate_patients(m.domains,1,seed=rng.randrange(2**50))[0]
        fixed=tuple(v if j%3==mask else -1 for j,v in enumerate(truth))
        noise=random.Random(seed*100000+i);outcomes={}
        for fact,domain in enumerate(m.domains):
            shared=noise.random()<rho;common_error=noise.random()<.05
            common_wrong=noise.choice([v for v in range(len(domain)) if v!=truth[fact]])
            for route,q in enumerate((.70,.95)):
                sufficient=noise.random()<q;error=common_error if shared else noise.random()<.05
                wrong=common_wrong if shared else noise.choice([v for v in range(len(domain)) if v!=truth[fact]])
                outcomes[fact,route]=-1 if not sufficient else wrong if error else truth[fact]
        if i in indices:found[i]=(data['groups'][group],m,fixed,truth,rho,outcomes)
    return found


def method_replay(model,trials,fixed,truth,outcomes,row):
    planner=RiskAwareAcquisition(model,risk_cutoff=.03,coupling=(0.,))
    state=planner.empty_state();visits=0;snapshots=[]
    labels_gold=model.statuses(truth)
    def snapshot(action=None):
        decisions=planner.decisions(fixed,state)
        observations=[]
        for f,code in enumerate(model.codes):
            if fixed[f]>=0:reports=[{'source':'처음부터 알려진 자료','value':model.domains[f][fixed[f]]}]
            else:reports=[{'source':('기존 기록','방문 확인')[r],'value':None if v<0 else model.domains[f][v]}
                          for r,v in enumerate(state[2*f:2*f+2]) if v!=-2]
            if reports:observations.append({'code':code,'name':NAMES.get(code,code),'unit':UNITS.get(code,''),'reports':reports})
        return {'step':len(snapshots),'visits':visits,'action':action,'observations':observations,
                'decisions':[{'trial_id':trials[d.trial]['nct_id'],'label':d.label,'witness':d.witness_label,
                 'score':d.model_error_upper,'reason':d.reason,
                 'pending_question':('관측 자료의 충돌·누락을 확인해야 합니다.' if d.witness_label<0 else '결론을 내리기 전 자료의 오류 가능성을 더 확인해야 합니다.') if d.label<0 else ''}
                 for d in decisions]}
    snapshots.append(snapshot())
    trace=json.loads(row['trace'])
    for step,(fact,route,report) in enumerate(trace):
        expected=planner.choose(fixed,state,1-visits,3-step,horizon=1 if row['method']=='risk_myopic' else 2)
        if expected!=(fact,route) or outcomes[fact,route]!=report:raise AssertionError('Frozen trace no longer matches the actual planner')
        recheck=state[2*fact]>=0 or state[2*fact+1]>=0
        state=state[:2*fact+route]+(report,)+state[2*fact+route+1:];visits+=route
        code=model.codes[fact]
        snapshots.append(snapshot({'fact':NAMES.get(code,code),'unit':UNITS.get(code,''),'route':route,
            'kind':'재확인' if recheck else '새 자료 획득','value':None if report<0 else model.domains[fact][report]}))
    final=tuple(d['label'] for d in snapshots[-1]['decisions'])
    if final!=tuple(json.loads(row['labels'])) or sum(a==b for a,b in zip(final,labels_gold))!=int(row['correct']):
        raise AssertionError('Displayed states differ from frozen evaluation')
    if planner.choose(fixed,state,1-visits,3-len(trace),horizon=1 if row['method']=='risk_myopic' else 2) is not None:
        raise AssertionError('Displayed stop is not the actual planner stop')
    planner.clear_caches()
    return {'snapshots':snapshots,'correct':int(row['correct']),'visits':visits,'actions':len(trace),
            'any_wrong':bool(int(row['any_wrong'])),'stop':'네 시험의 수치 부분조건 판정을 마쳤습니다.' if all(v>=0 for v in final) else '확인 예산이 끝나 일부 수치 부분조건을 보류했습니다.'}


def build(source:Path,rows_path:Path,summary_path:Path,output:Path):
    data=json.loads(source.read_text(encoding='utf-8'))
    with rows_path.open(encoding='utf-8-sig',newline='') as stream:selected=choose_cases(list(csv.DictReader(stream)))
    found=reconstruct(data,{int(patient.rsplit(':',1)[1]) for patient,_ in selected.values()})
    cases=[]
    for kind,(patient,pair) in selected.items():
        group,model,fixed,truth,rho,outcomes=found[int(patient.rsplit(':',1)[1])]
        trials=[t for t in group['trials'] if t['split']=='heldout']
        gold=reference_statuses(trials,{code:model.domains[i][truth[i]] for i,code in enumerate(model.codes)},set(model.codes))
        if gold!=model.statuses(truth):raise AssertionError('Independent numeric reference mismatch')
        cases.append({'kind':kind,'title':CASE_NAMES[kind],'patient':patient,'group':group['group_id'],'coupling':rho,
          'trials':[{'trial_id':t['nct_id'],'source_url':t['source_url'],'title':t['title'],
             'criteria':[{'fact':NAMES.get(c['fact_code'],c['fact_code']),'direction':c['kind'],'operator':c['operator'],
                          'threshold':c['threshold'],'unit':c['unit'],'source_text':c['source_text']} for c in t['criteria']]} for t in trials],
          'gold':list(gold),'truth':[{'name':NAMES.get(code,code),'value':model.domains[i][truth[i]],'unit':UNITS.get(code,'')} for i,code in enumerate(model.codes)],
          'methods':{method:method_replay(model,trials,fixed,truth,outcomes,pair[method]) for method in ('risk_myopic','risk_targeted')}})
    payload={'cases':cases,'summary':json.loads(summary_path.read_text(encoding='utf-8')),
       'selection_note':'집계 평가가 끝난 뒤 설명을 위해 조건별 가장 이른 에피소드를 골랐습니다. 효과 크기·사례 빈도를 추정하는 표본이 아닙니다.',
       'scope_question':'수치 외 진단·시점·검사법·수혈 등 원문 문맥은 따로 확인해야 합니다.',
       'disclaimer':DEFAULT_MEDICAL_DISCLAIMER}
    encoded=json.dumps(payload,ensure_ascii=False).replace('</','<\\/')
    template=Path(__file__).with_name('templates')/'risk_presentation_replay.html'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(template.read_text(encoding='utf-8').replace('__REPLAY_DATA__',encoded),encoding='utf-8')
    print(json.dumps({'output':str(output),'teaching_cases':[(c['kind'],c['patient']) for c in cases],'verified_method_traces':6},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('data/paper_risk_method_v1/protocol_subsets.json'))
    p.add_argument('--rows',type=Path,default=Path('runs/risk-method-20261023/matched/test/patient_results.csv'))
    p.add_argument('--summary',type=Path,default=Path('docs/internal/results/risk-aware-acquisition-v1/matched_test_summary.json'))
    p.add_argument('--output',type=Path,default=Path('docs/internal/demo/clarifytrial-risk-replay.html'))
    a=p.parse_args();build(a.source,a.rows,a.summary,a.output)
