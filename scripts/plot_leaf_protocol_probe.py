"""Publication figure separating development replay from fresh-clause calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development",type=Path,default=Path("docs/internal/results/leaf-protocol-v1"))
    parser.add_argument("--followup",type=Path,default=Path("docs/internal/results/leaf-protocol-holdout-v1"))
    args=parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    development=json.loads((args.development/"summary.json").read_text(encoding="utf-8"))
    followup=json.loads((args.followup/"summary.json").read_text(encoding="utf-8"))
    dev={r["method"]:r for r in development["metrics"]}
    test={r["method"]:r for r in followup["metrics"]}
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":12,"axes.spines.top":False,"axes.spines.right":False})
    fig,axes=plt.subplots(1,2,figsize=(11,4.9))
    panels=((axes[0],dev,("old","v8","v8_proposals_v9"),("Old calls","V8 calls","V9 replay"),
             ("#777777","#D55E00","#009E73"),"Development: 48 protocol clauses"),
            (axes[1],test,("old","v9"),("Old calls","V9 calls"),("#777777","#009E73"),"Fresh follow-up: 24 protocol clauses"))
    for ax,rows,methods,names,colors,title in panels:
        values=[100*rows[m]["macro_clause_agreement"] for m in methods]
        bars=ax.bar(range(len(methods)),values,color=colors,width=.6)
        if "v8_proposals_v9" in methods:
            bars[methods.index("v8_proposals_v9")].set_hatch("///")
            bars[methods.index("v8_proposals_v9")].set_edgecolor("#333333")
        ax.bar_label(bars,labels=[f"{v:.2f}%" for v in values],padding=5,fontsize=11)
        ax.set_xticks(range(len(methods)),names);ax.set_ylim(0,100);ax.set_title(title,fontsize=12,pad=15)
        ax.set_ylabel("Agreement with external reference (%)")
        ax.set_axisbelow(True);ax.grid(axis="y",alpha=.2)
    fig.subplots_adjust(left=.075,right=.99,bottom=.24,top=.86,wspace=.32)
    fig.text(.075,.055,"Equal weight per clause; only the bounded numeric interpretation is scored.\nHatched bar: post-hoc development replay, not a new model evaluation. Annotation agreement is not clinical accuracy.",fontsize=10)
    for suffix in ("png","svg"):
        path=args.development/("leaf_source_agreement."+suffix)
        fig.savefig(path,dpi=180,bbox_inches="tight")
        if suffix=="svg":path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines())+"\n",encoding="utf-8",newline="\n")
    plt.close(fig)


if __name__=="__main__":main()
