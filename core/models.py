# --- File: akita_geofence_notifier/akita_geofence_notifier/core/models.py ---
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class NodeInfo:
    """Represents the state of a node in the mesh."""
    node_id: str # e.g., '!a1b2c3d4'
    name: str = "Unknown"
    short_name: str = "?"
    hw_model: str = "Unknown HW"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None # Meters
    last_heard: Optional[float] = None # time.time() timestamp
    position_time: Optional[int] = None # GPS epoch time, if available
    battery_level: Optional[int] = None
    rssi: Optional[float] = None
    snr: Optional[float] = None

    def distance_to(self, other_node: 'NodeInfo') -> float:
        """Calculates distance to another NodeInfo object."""
        # Import locally to avoid circular dependency at module level
        from .distance import calculate_distance_km
        return calculate_distance_km(self.latitude, self.longitude,
                                     other_node.latitude, other_node.longitude)
