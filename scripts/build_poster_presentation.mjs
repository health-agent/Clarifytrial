/** Editable scientific poster from fixed held-out results. No new experiments. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const workspace = process.cwd();
const runtimeModules = process.env.CODEX_RUNTIME_NODE_MODULES || 'C:/Users/kll45/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const require = createRequire(path.join(runtimeModules, '..', 'poster-authoring.cjs'));
const {Presentation, PresentationFile} = await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const skill = 'C:/Users/kll45/.codex/plugins/cache/openai-primary-runtime/presentations/26.1020.11348/skills/presentations';
const {applyPresentationChartFont} = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')).href);
const output = path.resolve(process.argv[2] || 'runs/poster-20261023/draft');
await fs.mkdir(output, {recursive:true});
const source = path.join(workspace, 'docs/internal/results/risk-aware-acquisition-v1/matched_test_summary.json');
const summary = JSON.parse(await fs.readFile(source, 'utf8'));
const byMethod = Object.fromEntries(summary.metrics.map(m=>[m.method,m]));
const base=byMethod.risk_myopic, proposed=byMethod.risk_targeted;
const difference=100*(proposed.correct-base.correct)/(4*summary.episodes);
const visitReduction=100*(base.visits-proposed.visits)/base.visits;
if(summary.episodes!==1200 || base.trials!==4800 || proposed.trials!==4800) throw new Error('Unexpected result scope');

// 1200 x 1600 design grid, physically 900 x 1200 mm. Official size still pending.
const S=900/25.4*96/1200, FONT='Malgun Gothic';
const C={ink:'#183249',muted:'#4D6170',teal:'#087F82',gray:'#8C9AA5',line:'#CBDBDF',pale:'#ECF7F6',red:'#9A463D',white:'#FFFFFF'};
const presentation=Presentation.create({slideSize:{width:1200*S,height:1600*S}});
const slide=presentation.slides.add(); slide.background.fill=C.white;
const layout={width:1200,height:1600,physical_mm:[900,1200],font:FONT,elements:[]};
const rect=(x,y,w,h)=>({left:x*S,top:y*S,width:w*S,height:h*S});
function text(name,copy,x,y,w,h,size=18,bold=false,color=C.ink){
  layout.elements.push({kind:'text',name,text:copy,x,y,w,h,size,bold,color});
  const shape=slide.shapes.add({name,geometry:'textbox',position:rect(x,y,w,h),fill:'none',line:{fill:'none',width:0}});
  shape.text=[[{run:copy,style:{typeface:FONT,fontSize:`${size*S}px`,bold,color}}]];
  shape.text.style={fontSize:size*S,bold,color,verticalAlignment:'top',insets:0};
  return shape;
}
function rule(y,x=48,w=1104,color=C.line){layout.elements.push({kind:'rule',x,y,w,color});slide.shapes.add({name:`rule-${x}-${y}`,geometry:'rect',position:rect(x,y,w,1.2),fill:color,line:{fill:'none',width:0}});}
function heading(name,copy,x,y,w=510){text(name,copy,x,y,w,42,25,true,C.teal);rule(y+45,x,w);}
function table(name,values,x,y,width,height,widths){
  layout.elements.push({kind:'table',name,values,x,y,w:width,h:height,widths});
  const t=slide.tables.add({rows:values.length,columns:values[0].length,left:x*S,top:y*S,width:width*S,height:height*S,
    columnWidths:widths.map(v=>v*S),values});
  t.name=name;
  t.borders.assign({fill:C.line,width:.6*S,style:'solid'});
  for(let r=0;r<values.length;r++){
    t.rows[r].height=height*S/values.length;
    for(let c=0;c<values[0].length;c++){
      const cell=t.getCell(r,c),highlight=r===values.length-1 && name==='all-baselines';
      cell.fill=r===0?C.ink:highlight?C.pale:C.white;
      cell.value=[[{run:String(values[r][c]),style:{typeface:FONT,fontSize:`${16*S}px`,bold:r===0||highlight,color:r===0?C.white:C.ink}}]];
      cell.text.style={fontSize:16*S,insets:{left:10*S,right:7*S,top:7*S,bottom:5*S}};
    }
  }
  return t;
}
function comparisonChart(name,x,y,w,h,values,max,unit,format){
  layout.elements.push({kind:'chart',name,x,y,w,h,values,max,unit,format});
  const chart=slide.charts.add('bar',{name,position:rect(x,y,w,h),categories:[name==='correct-subset-states'?'선택한 수치 부분조건':'합성 평가 환자'],
    series:[{name:'한 단계 선택',values:[values[0]],fill:C.gray},{name:'선택적 재확인',values:[values[1]],fill:C.teal}],
    barOptions:{direction:'bar',grouping:'clustered',gapWidth:80},
    hasLegend:true,legend:{position:'bottom',textStyle:{fontSize:15*S,fill:C.muted}},
    xAxis:{visible:false},
    yAxis:{min:0,max,majorUnit:max===100?20:300,numberFormatCode:'0',title:unit,textStyle:{fontSize:14*S,fill:C.muted},majorGridlines:{fill:C.line,width:.5*S}},chartFill:C.white,plotAreaFill:C.white,
    dataLabels:{showValue:true,position:'outEnd',numberFormatCode:format,textStyle:{fontSize:20*S,bold:true,fill:C.ink}}});
  applyPresentationChartFont(chart,{fontFamily:FONT});
  return chart;
}

text('project','ClarifyTrial',48,29,650,34,24,true,C.teal);
text('scope-label','공개 임상시험 조건과 합성 환자를 이용한 방법 연구',630,34,522,30,15,false,C.muted);
text('accepted-title','제한된 확인 횟수와 환자 제약을 고려한\n상호작용형 임상시험 사전선별: ClarifyTrial',48,82,1104,120,39,true);
text('authors','김군협¹, 정승진¹, 오상준¹, 채인영¹, 김서환¹, 신민영¹, 이수지¹*',48,210,1104,33,18);
text('affiliation','¹성균관대학교 공과대학 시스템경영공학과, 대한민국',48,246,1104,31,16,false,C.muted);
rule(292,48,1104,C.teal);
text('extension-title','추가 연구: 관측 근거와 위험 교정을 이용한 선택적 재확인',48,313,1104,45,27,true,C.teal);
text('main-finding','같은 위험 교정 목표에서 올바른 판정을 늘리고 방문을 줄였습니다.',48,369,1104,42,25,true);

const L=48,R=620,LW=520,RW=532;
heading('problem','문제와 연구 질문 [1]',L,444,LW);
text('problem-body','첫 기록을 바로 믿으면 잘못 판정할 수 있습니다.\n모든 자료를 두 번 확인하면 다른 후보를 볼 기회를 잃습니다.\n어떤 자료를 얻고, 어느 자료를 다시 확인해야 할까요?',L,507,LW,105,18);

heading('method','방법: 필요한 위치만 재확인',L,608,LW);
text('step-one','1. 관측된 자료가 결론을 뒷받침하는가?',L,672,LW,35,20,true);
text('step-one-detail','실제 보고가 조건 충족·위반의 논리 근거여야 합니다.\n없는 값을 예측해 채운 것만으로 결론을 내리지 않습니다.',L+21,712,LW-21,66,17);
text('step-two','2. 오류 점수가 충분히 낮은가?',L,790,LW,35,20,true);
text('step-two-detail','반복된 같은 검사값은 한 번만 적분합니다.\n점수가 기준을 넘으면 결론을 보류합니다.',L+21,830,LW-21,62,17);
text('step-three','3. 다음 자료 획득과 재확인을 함께 선택',L,908,LW,35,20,true);
text('step-three-detail','남은 확인 횟수와 방문 한도에서 두 단계 앞을 계산합니다.\n관측 후 상태를 갱신하고 다시 선택하거나 종료합니다.',L+21,948,LW-21,67,17);

heading('calibration','질문과 종료를 포함한 정책 전체 교정 [4]',L,1045,LW);
text('calibration-body','질문·종료 규칙을 먼저 고정했습니다.\n합성 환자 1,200명으로 정책을 교정했습니다.\n목표는 새 결론 환자에서 하나라도 오판할 위험 5%입니다.\n다른 합성 환자 1,200명에서 별도로 평가했습니다.\n비교군의 모형·판정기·확인 및 방문 한도는 같습니다.',L,1108,LW,126,17);
text('method-origin','방법의 위치 [2–4]\n결정 중심 정보 획득·선택적 예측의 원리를 사용했습니다.\n관측 근거 요구와 선택적 재확인을 결합한 문제별 방법입니다.',L,1250,LW,96,17,false,C.muted);

heading('results','결과: 동일 모형의 별도 합성 환자 비교',R,444,RW);
text('correct-difference',`판정 일치 +${difference.toFixed(2)}%포인트`,R,505,RW,52,32,true,C.teal);
text('correct-ci','짝지은 환자 재표집 95% 구간 +2.29~+3.90%포인트',R,564,RW,30,15,false,C.muted);
comparisonChart('correct-subset-states',R,606,RW,190,[100*base.recovery,100*proposed.recovery],100,'올바른 부분조건 상태 (%)','0.00');
text('visits-difference',`방문 ${visitReduction.toFixed(1)}% 감소`,R,830,RW,50,32,true,C.teal);
comparisonChart('visits-per-patient-cohort',R,886,RW,190,[base.visits,proposed.visits],1200,'방문 횟수 (1,200명 합계)','0');
table('all-baselines',[
  ['정보 선택 방법','올바른 상태','방문'],
  ['한 단계 선택',`${(100*base.recovery).toFixed(2)}%`,String(base.visits)],
  ['목표 정보량',`${(100*byMethod.risk_entropy.recovery).toFixed(2)}%`,String(byMethod.risk_entropy.visits)],
  ['Bayes 오류 감소',`${(100*byMethod.risk_bayes.recovery).toFixed(2)}%`,String(byMethod.risk_bayes.visits)],
  ['선택적 재확인',`${(100*proposed.recovery).toFixed(2)}%`,String(proposed.visits)]
],R,1105,RW,180,[270,145,117]);
text('denominator','새 공개 시험 24건·수치 비교 58개. 평가 1,200명 × 4시험 = 4,800상태.\n초기부터 맞힌 상태를 공통 포함. 확인 최대 3회·추가 방문 최대 1회.',R,1300,RW,60,14,false,C.muted);

rule(1380);
text('limits-title','해석과 한계',48,1399,190,33,20,true,C.red);
text('limits','오판 환자 23/1,046명에서 26/1,064명, 전체 행동 2,957회에서 3,022회로 증가했습니다.\n같은 위험 목표는 실제 오류율의 동등성을 뜻하지 않습니다. 분포·오류 변화에서는 교정 목표를 넘었습니다.\n선택한 수치 부분조건의 합성 결과이며 전체 임상 적격·환자 편익은 검증하지 않았습니다.',235,1399,917,81,14,false,C.muted);
text('references','[1] Liu et al. DQueST. JAMIA 2019. doi:10.1093/jamia/ocz121\n[2] Javdani et al. Decision Making. AISTATS 2014;33:430–438.',48,1490,538,43,11.5,false,C.muted);
text('references-two','[3] Ma et al. EDDI. ICML 2019;97:4234–4243.\n[4] Angelopoulos et al. Learn then Test. arXiv:2110.01052v5.',620,1490,532,43,11.5,false,C.muted);
text('disclaimer','이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다.',48,1550,1104,39,11.5,false,C.muted);
slide.speakerNotes.text=[
  '선정 제목·저자는 2026-09-05 HWP 내용 기록을 유지했다. 새 방법은 선정 후 추가 연구다.',
  '작업 크기 900×1200 mm. 학회 공식 규격·발표시간·추가 결과 반영 범위 확인 전 인쇄/제출 확정본이 아니다.',
  '수치 출처: docs/internal/results/risk-aware-acquisition-v1/matched_test_summary.json 및 matched_analysis.json.',
  '첫 결과를 본 후 확률모형을 맞춘 후속 비교다. 방법·τ=.03은 유지하고 새 교정/평가를 사용했다. 첫 교정의 보장을 재사용하지 않았다.',
  '[1] Liu et al. DQueST. JAMIA 2019;26:1333–1343. https://doi.org/10.1093/jamia/ocz121',
  '[2] Javdani et al. Near Optimal Bayesian Active Learning for Decision Making. AISTATS 2014;33:430–438. https://proceedings.mlr.press/v33/javdani14.html',
  '[3] Ma et al. EDDI. ICML 2019;97:4234–4243. https://proceedings.mlr.press/v97/ma19c.html',
  '[4] Angelopoulos et al. Learn then Test. https://arxiv.org/html/2110.01052v5',
  '임상적 사용: 이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다.',
].join('\n');

await (await PresentationFile.exportPptx(presentation)).save(path.join(output,'clarifytrial-poster.pptx'));
await fs.writeFile(path.join(output,'clarifytrial-poster.png'),new Uint8Array(await (await presentation.export({slide,format:'png',scale:.5})).arrayBuffer()));
// PDF export in this bundled runtime cannot load its standard-font data.
// The companion ReportLab builder uses this same authored content and geometry.
await fs.writeFile(path.join(output,'poster-layout.json'),JSON.stringify(layout,null,2),'utf8');
console.log(JSON.stringify({output,physical_mm:[900,1200],slides:1,nativeCharts:2,nativeTables:1,source,correct_difference_pp:difference,visit_reduction_percent:visitReduction}));
