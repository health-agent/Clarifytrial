"""Scientific figure: source-comparison errors, recovery, and actual visits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,default=Path("docs/internal/results/corroboration-v1"))
    args=parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data=json.loads((args.results/"summary.json").read_text(encoding="utf-8"))
    rows={(r["setting"],r["policy"]):r for r in data["metrics"] if r["budget"]==3 and r["visit_limit"]==1}
    settings=("independent","shared_half","shared_all","copied_source")
    labels=("Independent","50% shared","Fully shared","Copied origin")
    policies=("trust_first","file_quorum","two_step_sources","budget_sources")
    names=("First source","Count files","Two-step sources","Budget sources")
    colors=("#666666","#D55E00","#56B4E9","#009E73")
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,3,figsize=(15,4.8))
    panels=(("Correct subset states (%)",lambda r:100*r["recovery"],100),
            ("Wrong subset decisions (of 2,304)",lambda r:r["unsafe"],55),
            ("Visits (across 576 settings)",lambda r:r["visits"],520))
    for ax,(ylabel,getter,upper) in zip(axes,panels):
        for j,(policy,name,color) in enumerate(zip(policies,names,colors)):
            x=[i+(j-1.5)*.18 for i in range(4)]
            values=[getter(rows[(s,policy)]) for s in settings]
            bars=ax.bar(x,values,width=.17,color=color,label=name)
            if policy in {"trust_first","budget_sources"}:
                ax.bar_label(bars,labels=[f"{v:.1f}" if upper==100 else str(int(v)) for v in values],padding=3,fontsize=9)
        ax.set_xticks(range(4),labels,rotation=12)
        ax.set_ylabel(ylabel)
        ax.set_ylim(0,upper)
        ax.set_axisbelow(True);ax.grid(axis="y",alpha=.2)
    handles,legend=axes[0].get_legend_handles_labels()
    fig.legend(handles,legend,ncol=4,loc="upper center",bbox_to_anchor=(.5,1.01),frameon=False)
    fig.subplots_adjust(left=.055,right=.99,bottom=.22,top=.88,wspace=.32)
    fig.text(.055,.045,"Synthetic fixed values; 3 attempts; at most 1 visit. Initial evidence is already corroborated.\nSource agreement is not proof of truth. Copied-origin source policies acquire no new usable evidence.",fontsize=10)
    for suffix in ("png","svg"):
        path=args.results/("corroboration_tradeoff."+suffix)
        fig.savefig(path,dpi=180,bbox_inches="tight")
        if suffix=="svg":
            path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines())+"\n",encoding="utf-8",newline="\n")
    plt.close(fig)


if __name__=="__main__":main()
