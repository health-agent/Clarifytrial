"""Visualize per-protocol heterogeneity and the resampling unit sensitivity."""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"docs/internal/results/paper-logic-v2"
summary=json.loads((OUT/"summary.json").read_text(encoding="utf-8"))
analysis=json.loads((OUT/"protocol_sensitivity.json").read_text(encoding="utf-8"))
comparison=next(c for c in analysis["comparisons"] if c["baseline"]=="decision_myopic")
patient=next(c for c in summary["comparisons"] if c["setting"]=="imperfect" and c["visit_limit"]==1 and c["comparator"]=="decision_myopic")
nonzero=sorted((t for t in comparison["trial_effects"] if t["difference"]),key=lambda t:t["difference"])
fig,(left,right)=plt.subplots(1,2,figsize=(11,5.7),gridspec_kw={"width_ratios":[1.3,1]})
fig.subplots_adjust(left=.13,right=.97,top=.84,bottom=.24,wspace=.48)
values=[100*t["difference"] for t in nonzero]
left.barh(range(len(values)),values,color=["#a9513e" if v<0 else "#246979" for v in values])
left.set_yticks(range(len(values)),[t["trial_id"] for t in nonzero],fontsize=9)
left.axvline(0,color="#555",lw=.8)
left.set(xlabel="Change in correct subset states (pp)",title="9 protocols changed; 15 tied",xlim=(-27,25))
for i,v in enumerate(values):left.text(v+(.6 if v>=0 else -.6),i,f"{v:+.1f}",ha="left" if v>=0 else "right",va="center",fontsize=8)
point=100*comparison["crossed_resampling"]["mean_difference"]
intervals=[patient["effect"]["bootstrap_95_ci"],comparison["crossed_resampling"]["percentile_95_range"]]
for i,interval in enumerate(intervals):
    lo,hi=100*interval["lower"],100*interval["upper"]
    right.plot([lo,hi],[i,i],color="#246979",lw=3)
    right.scatter([point],[i],color="#246979",s=45,zorder=3)
    right.text((lo+hi)/2,i+.18,f"[{lo:+.2f}, {hi:+.2f}]",ha="center",fontsize=9)
right.axvline(0,color="#777",ls="--",lw=1)
right.set_yticks([0,1],["Patient only","Patient + protocol"],fontsize=9)
right.set(xlabel="Overall difference (pp)",title="95% resampling ranges",ylim=(-.5,1.6),xlim=(-2.5,5.3))
for ax in (left,right):ax.spines[["top","right"]].set_visible(False)
fig.suptitle("Average improvement does not imply uniform protocol benefit",fontsize=14,y=.97)
fig.text(.55,.04,"Fallible-route budget planning minus one-step selection assuming successful answers.\n24 reused protocols; 288 synthetic vectors × 2 masks; 3 attempts; ≤1 visit. Selected clauses only.",ha="center",fontsize=9,color="#555")
fig.savefig(OUT/"protocol_heterogeneity.png",dpi=170,bbox_inches="tight")
fig.savefig(OUT/"protocol_heterogeneity.svg",bbox_inches="tight")
