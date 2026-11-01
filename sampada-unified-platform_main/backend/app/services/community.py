"""Community-aware oversupply signalling.

This is the project's central research idea, and it is the place where being
honest matters most.

The design conversations went round this loop several times: recommending the
most profitable crop to everyone causes oversupply, which destroys the very
price that made it attractive. The agreed conclusion was NOT that the system can
prevent overproduction - it cannot, because it does not control farmers who do
not use it and cannot see what anyone is planting. The agreed conclusion was:

    "Our system reduces the probability and impact of overproduction among
     participating farmers."

So this module deliberately computes a *narrow, truthful* signal:

    app_issued_area[crop] = hectares this app has already recommended for that
                            crop, in that district, for that season (rank 1 only)

    app_share[crop]       = app_issued_area[crop] / total app-issued area

    relative_crowding     = app_share, compared against an even split
    saturation_score      = 1 - relative_crowding   (higher = less crowded)

What this is NOT: total district production, real demand, actual planted area,
or a market price forecast. It covers only farms using this app. When there is
no app-issued history at all, this module reports *unavailable* and the
recommendation engine redistributes the weight rather than inventing a number.

A true saturation model needs real demand and production figures. Those are not
available in this repository, so they are not simulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import settings


@dataclass(slots=True)
class CommunitySignal:
    available: bool
    scores: dict[str, float] = field(default_factory=dict)
    issued_area_ha: dict[str, float] = field(default_factory=dict)
    shares: dict[str, float] = field(default_factory=dict)
    total_issued_area_ha: float = 0.0
    reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def score_for(self, crop_id: str, candidate_count: int) -> float | None:
        """Saturation factor for one crop, or None when unavailable."""
        if not self.available or crop_id not in self.scores:
            return None
        return self.scores[crop_id]


def compute_signal(
    issued_area: dict[str, float],
    candidate_crop_ids: list[str],
) -> CommunitySignal:
    """Turn app-issued area into per-crop saturation factors."""
    total = sum(issued_area.values())

    if total <= 0:
        return CommunitySignal(
            available=False,
            reason="no crops have been recommended through this app yet for this season and district",
            notes=[
                "regional production, demand and adoption figures are not available to this system",
                "a true oversupply model needs real supply and demand data, which this MVP does not have",
            ],
        )

    shares = {crop_id: area / total for crop_id, area in issued_area.items()}

    # Reference point: what share would each candidate get if the app spread its
    # recommendations evenly across them? A crop holding more than that is
    # relatively crowded; one holding less is relatively free.
    candidate_crop_ids = [crop_id for crop_id in candidate_crop_ids] or list(shares)
    even_share = 1.0 / max(1, len(candidate_crop_ids))

    threshold = float(settings.get("recommendation.community_oversupply_threshold", 0.85))
    scores: dict[str, float] = {}
    for crop_id in candidate_crop_ids:
        share = shares.get(crop_id, 0.0)
        if share <= 0:
            # Nothing issued for this crop yet: least crowded by definition.
            scores[crop_id] = 1.0
            continue
        # Ratio of this crop's share to an even split, compressed to 0..1.
        crowding = share / even_share if even_share > 0 else 0.0
        # Above the configured threshold the crop is treated as fully saturated.
        normalised = crowding / max(threshold, 1e-6)
        scores[crop_id] = round(max(0.0, min(1.0, 1.0 - (normalised / 2.0))), 4)

    return CommunitySignal(
        available=True,
        scores=scores,
        issued_area_ha=issued_area,
        shares={key: round(value, 4) for key, value in shares.items()},
        total_issued_area_ha=round(total, 4),
        notes=[
            "SCOPE: covers only farms using this app, not the whole district",
            "this is relative crowding, not verified market oversupply",
            "app-issued area is not the same as area actually planted",
        ],
    )


def oversupply_risk(signal: CommunitySignal, crop_id: str, candidate_count: int) -> float | None:
    """0..1 risk that app-driven crowding is already a problem for this crop."""
    score = signal.score_for(crop_id, candidate_count)
    if score is None:
        return None
    return round(1.0 - score, 4)
