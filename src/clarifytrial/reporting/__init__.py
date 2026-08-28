"""Patient-facing and detailed reporting built from validated decisions."""

from .boundary_differences import build_ineligible_boundary_differences
from .budget_frontier import build_budget_frontier
from .architecture_comparison import build_architecture_comparison
from .recommendations import build_recommendation_views
from .terminal_summary import build_terminal_summary_lines
from .readiness import build_final_evaluation_readiness
from .research_report import build_research_report

__all__ = [
    "build_ineligible_boundary_differences",
    "build_budget_frontier",
    "build_architecture_comparison",
    "build_recommendation_views",
    "build_terminal_summary_lines",
    "build_final_evaluation_readiness",
    "build_research_report",
]
