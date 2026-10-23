"""Publication-style plots of held-out same-belief comparisons only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR",str(Path("runs/risk-method-20261023/mpl").resolve()))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot(root, output):
    methods=("risk_myopic","risk_entropy","risk_bayes","risk_targeted")
    labels=("One-step verification","Target information gain","Bayes-error reduction","Targeted verification")
    phases=(("Coupling-model set: first held-out evaluation",root/"test"/"summary.json"),
            ("Independent model: fresh matched comparison",root/"matched"/"test"/"summary.json"))
    colors=["#9ba9b3"]*3+["#147d92"]
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(2,3,figsize=(13,7.6),gridspec_kw={"width_ratios":[1.25,1.,1.1]})
    for row,(title,path) in enumerate(phases):
        data=json.loads(path.read_text(encoding="utf-8"));by_method={m["method"]:m for m in data["metrics"]}
        values=[by_method[m] for m in methods];y=list(range(4))
        for ax in axes[row]:ax.invert_yaxis();ax.grid(axis="x",alpha=.18);ax.set_axisbelow(True)
        ax=axes[row,0]
        bars=ax.barh(y,[100*m["recovery"] for m in values],color=colors,height=.56)
        ax.set_yticks(y,labels);ax.set_xlim(0,100);ax.set_xlabel("Correct subset states (%)")
        for bar,m in zip(bars,values):ax.text(bar.get_width()+1,bar.get_y()+bar.get_height()/2,
            f'{100*m["recovery"]:.2f}',va="center",fontsize=9)
        ax.set_title(title,loc="left",fontsize=11,pad=15)
        ax=axes[row,1]
        bars=ax.barh(y,[m["visits"]/m["episodes"] for m in values],color=colors,height=.56)
        ax.set_yticks(y,[]);ax.set_xlim(0,1.08);ax.set_xlabel("Visits per episode")
        for bar,m in zip(bars,values):ax.text(bar.get_width()+.015,bar.get_y()+bar.get_height()/2,
            f'{m["visits"]/m["episodes"]:.3f}',va="center",fontsize=9)
        ax=axes[row,2]
        for j,m in enumerate(values):
            point=100*m["selective_episode_risk"];upper=100*m["selective_episode_risk_upper_95"]
            ax.plot([point,upper],[j,j],color=colors[j],lw=2)
            ax.plot(point,j,"o",color=colors[j]);ax.plot(upper,j,"|",color=colors[j],ms=10)
            ax.text(upper+.12,j,f'{point:.2f} [{upper:.2f}]',va="center",fontsize=8)
        ax.set_yticks(y,[]);ax.set_xlim(0,6.3);ax.axvline(5,color="#a44a46",ls="--",lw=1)
        ax.set_xlabel("Any-error / selected episodes (%)\npoint [one-sided 95% upper]")
    fig.suptitle("Targeted verification under a shared calibration risk target",fontsize=15,y=.99)
    fig.text(.02,.012,"Independent synthetic episodes; fixed selected numeric criteria; initial correct states included.\n"
        "Calibration target: conditional episode-any-error <=5%, delta=5%. Not full clinical eligibility or an OOD guarantee.",fontsize=9)
    fig.tight_layout(rect=(0,.075,1,.95));output.mkdir(parents=True,exist_ok=True)
    for suffix in ("png","svg"):fig.savefig(output/f"risk_targeted_verification.{suffix}",dpi=180,bbox_inches="tight")
    plt.close(fig)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=Path("runs/risk-method-20261023"))
    p.add_argument("--output",type=Path,default=Path("docs/internal/results/risk-aware-acquisition-v1"));a=p.parse_args();plot(a.root,a.output)
