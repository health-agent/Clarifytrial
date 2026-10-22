"""Scientific figure of coverage, erroneous decisions, and actual visit burden."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,required=True)
    args=parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    summary=json.loads((args.results/"summary.json").read_text(encoding="utf-8"))
    rows={(r["setting"],r["policy"]):r for r in summary["metrics"]}
    settings=("independent_p05","shared_all_p05","independent_p15","shared_all_p15")
    labels=("Independent\np = .05","Shared\np = .05","Independent\np = .15","Shared\np = .15")
    policies=("trust_first","exact_sources","direction_same_trace","direction_two_step","direction_sources")
    names=("First source","Exact-value plan","Direction / fixed actions","Direction / two-step","Direction / budget")
    colors=("#777777","#D55E00","#E69F00","#56B4E9","#009E73")
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,3,figsize=(16,5.2))
    panels=(("Correct subset states (%)",lambda r:100*r["recovery"],100),
            ("Wrong subset decisions (of 2,304)",lambda r:r["unsafe"],140),
            ("Visits (across 576 settings)",lambda r:r["visits"],510))
    for ax,(ylabel,getter,upper) in zip(axes,panels):
        for j,(policy,name,color) in enumerate(zip(policies,names,colors)):
            values=[getter(rows[(s,policy)]) for s in settings]
            bars=ax.bar([i+(j-2)*.15 for i in range(4)],values,width=.14,color=color,label=name)
            if policy=="direction_sources":
                ax.bar_label(bars,labels=[f"{v:.1f}" if upper==100 else str(int(v)) for v in values],padding=3,fontsize=9)
        ax.set_xticks(range(4),labels)
        ax.set_ylabel(ylabel);ax.set_ylim(0,upper)
        ax.set_axisbelow(True);ax.grid(axis="y",alpha=.2)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,ncol=3,loc="upper center",bbox_to_anchor=(.5,1.02),frameon=False,fontsize=10)
    fig.subplots_adjust(left=.055,right=.99,bottom=.24,top=.82,wspace=.33)
    fig.text(.055,.045,"Exploratory reuse of 24 protocol subsets and 288 synthetic vectors; 3 attempts, at most 1 visit.\nWrong values are sampled uniformly from other grid values. Initial paired evidence is correct. Agreement does not prove truth.",fontsize=10)
    for suffix in ("png","svg"):
        path=args.results/("direction_tradeoff."+suffix)
        fig.savefig(path,dpi=180,bbox_inches="tight")
        if suffix=="svg":
            path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines())+"\n",encoding="utf-8",newline="\n")
    plt.close(fig)


if __name__=="__main__":main()
