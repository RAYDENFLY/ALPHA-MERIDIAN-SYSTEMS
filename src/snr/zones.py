"""Support and Resistance Zone Clustering and Construction.

Clusters historical swing levels into price zones without lookahead bias.
"""

from typing import List
import numpy as np
from src.core.types import SnRZone, SwingPoint


def cluster_swing_zones(
    swings: List[SwingPoint],
    tolerance_pct: float = 0.015,
) -> List[SnRZone]:
    """Group historical swing prices into clusters based on proximity tolerance.

    Parameters:
    -----------
    swings : List[SwingPoint]
        Historical confirmed swings.
    tolerance_pct : float
        Percentage price tolerance (e.g. 0.015 = 1.5%) for grouping nearby swings.

    Returns:
    --------
    List[SnRZone]
        List of clustered S/R zones.
    """
    if not swings:
        return []

    # Sort swings by price
    sorted_swings = sorted(swings, key=lambda s: s.price)
    clusters: List[List[SwingPoint]] = []

    current_cluster: List[SwingPoint] = [sorted_swings[0]]

    for s in sorted_swings[1:]:
        cluster_mean = sum(x.price for x in current_cluster) / len(current_cluster)
        distance_pct = abs(s.price - cluster_mean) / cluster_mean

        if distance_pct <= tolerance_pct:
            current_cluster.append(s)
        else:
            clusters.append(current_cluster)
            current_cluster = [s]

    if current_cluster:
        clusters.append(current_cluster)

    zones: List[SnRZone] = []
    for idx, cluster in enumerate(clusters):
        prices = [s.price for s in cluster]
        center = float(np.mean(prices))
        low = float(min(prices))
        high = float(max(prices))
        # Ensure zone has non-zero width
        if high == low:
            width = center * 0.002
            low = center - width
            high = center + width

        latest_interaction = max(s.confirmed_timestamp_ms for s in cluster)
        zones.append(
            SnRZone(
                zone_id=f"zone_{idx + 1}_{int(center)}",
                price_center=center,
                price_low=low,
                price_high=high,
                touch_count=len(cluster),
                rejection_count=0,
                breakout_count=0,
                last_interaction_timestamp_ms=latest_interaction,
                is_active=True,
                strength=float(len(cluster)),
            )
        )

    return zones
