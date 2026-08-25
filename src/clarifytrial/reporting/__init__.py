"""Patient-facing and detailed reporting built from validated decisions."""

from .boundary_differences import build_ineligible_boundary_differences
from .recommendations import build_recommendation_views
from .readiness import build_final_evaluation_readiness
from .research_report import build_research_report

__all__ = [
    "build_ineligible_boundary_differences",
    "build_recommendation_views",
    "build_final_evaluation_readiness",
    "build_research_report",
]
