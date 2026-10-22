"""Scientific figure from the recorded exploratory cost sensitivity (matplotlib)."""
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"docs/internal/results/paper-acquisition-v1"
rows=list(csv.DictReader((OUT/"visit_penalty_metrics.csv").open(encoding="utf-8-sig")))
subset={r["policy"]:r for r in rows if r["setting"]=="imperfect" and r["visit_limit"]=="1" and r["budget"]=="3"}
labels={"decision_myopic":"One-step, assumes success", "decision_budget":"Budget plan, assumes success",
        "quality_myopic":"One-step, fallible routes", "quality_budget":"Budget plan, fallible routes",
        "quality_penalty_0.1":"Visit penalty 0.10", "quality_penalty_0.25":"Visit penalty 0.25", "quality_penalty_0.5":"Visit penalty 0.50"}
fig,ax=plt.subplots(figsize=(10,5.8))
fig.subplots_adjust(left=.10,right=.98,top=.89,bottom=.23)
for i,(key,label) in enumerate(labels.items()):
    r=subset[key]
    x=int(r["visits"])/int(r["replays"])
    y=100*float(r["recovery"])
    ax.scatter(x,y,s=65,color="#c06c2b" if "penalty" in key else "#236779",marker="s" if "penalty" in key else "o")
    offset={"quality_penalty_0.1":(-90,18),"quality_budget":(8,-20),"quality_penalty_0.25":(16,-32),
            "quality_penalty_0.5":(-16,32),"decision_myopic":(12,8),"decision_budget":(12,-20)}.get(key,(8,8))
    ax.annotate(label,(x,y),xytext=offset,textcoords="offset points",fontsize=9,
                arrowprops=dict(arrowstyle="-",color="#aaa",lw=.6))
ax.set(xlabel="Additional visits per synthetic patient-mask setting",ylabel="Correct selected-subset states (%)",
       title="More confirmation can require more visits",xlim=(.22,1.2),ylim=(86.6,90.7))
ax.spines[["top","right"]].set_visible(False)
ax.grid(axis="y",alpha=.2)
fig.text(.53,.025,"24 heldout protocols; 288 synthetic vectors × 2 masks; 3 attempts; ≤1 visit.\nSufficient-answer chance: record 0.70, verification 0.95. Fixed numeric subsets, not full eligibility.",ha="center",fontsize=9,color="#555")
fig.savefig(OUT/"visit_frontier.png",dpi=170,bbox_inches="tight")
fig.savefig(OUT/"visit_frontier.svg",bbox_inches="tight")
