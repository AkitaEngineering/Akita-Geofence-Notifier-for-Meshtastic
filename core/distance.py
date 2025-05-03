# --- File: akita_geofence_notifier/akita_geofence_notifier/core/distance.py ---
import math
import logging
from .config import config # Import the loaded config

logger = logging.getLogger(__name__)

def calculate_distance_km(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float:
    """
    Calculates the distance between two lat/lon points using the Haversine formula.
    Returns float('inf') if any coordinate is None or invalid.
    """
    if None in [lat1, lon1, lat2, lon2]:
        # logger.debug("Cannot calculate distance with missing coordinates.")
        return float('inf')

    # Ensure coordinates are floats before calculations
    try:
        lat1_f, lon1_f, lat2_f, lon2_f = map(float, [lat1, lon1, lat2, lon2])
    except (ValueError, TypeError):
        logger.warning(f"Invalid coordinate types for distance calculation: ({lat1}, {lon1}), ({lat2}, {lon2})")
        return float('inf')

    # Convert degrees to radians
    dlat = math.radians(lat2_f - lat1_f)
    dlon = math.radians(lon2_f - lon1_f)
    lat1_rad = math.radians(lat1_f)
    lat2_rad = math.radians(lat2_f)

    # Haversine formula
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = config.geodetic_radius_km * c
    # logger.debug(f"Calculated distance: {distance:.3f} km")
    return distance
