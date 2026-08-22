"""Patient-facing and detailed reporting built from validated decisions."""

from .boundary_differences import build_ineligible_boundary_differences
from .recommendations import build_recommendation_views

__all__ = [
    "build_ineligible_boundary_differences",
    "build_recommendation_views",
]
