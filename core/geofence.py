# --- File: akita_geofence_notifier/akita_geofence_notifier/core/geofence.py ---
import logging
from typing import Dict, Set, List, Tuple
from .config import config, GeofenceConfig
from .distance import calculate_distance_km
from .models import NodeInfo # Assuming NodeInfo holds id, name, lat, lon

logger = logging.getLogger(__name__)

class GeofenceModule:
    """Manages geofences and checks node positions against them."""
    def __init__(self):
        # Initialize from the global config object directly
        self.geofences: List[GeofenceConfig] = config.geofences
        # Track which nodes are currently inside which geofences
        # {node_id: {geofence_name, ...}}
        self._nodes_inside: Dict[str, Set[str]] = {}
        logger.info(f"Geofence module initialized with {len(self.geofences)} geofences.")

    def check_node(self, node: NodeInfo) -> List[str]:
        """
        Checks a node's position against all geofences.
        Updates internal state and returns a list of notification messages
        for state changes (enter/exit).
        """
        notifications = []
        if node.latitude is None or node.longitude is None:
            # logger.debug(f"Cannot check geofence for node {node.node_id}: missing position.")
            # If node was previously inside, mark it as exited due to unknown position.
            if node.node_id in self._nodes_inside:
                 previously_inside_fences = self._nodes_inside.pop(node.node_id)
                 node_name = node.name or f"Node {node.node_id[-4:]}"
                 for fence_name in previously_inside_fences:
                      msg = f"{node_name} ({node.node_id}) exited geofence '{fence_name}' (position unknown)."
                      logger.info(msg)
                      notifications.append(msg)
            return notifications

        node_id = node.node_id
        node_name = node.name or f"Node {node_id[-4:]}" # Use name or fallback

        currently_inside_fences: Set[str] = set()
        previously_inside_fences = self._nodes_inside.get(node_id, set())

        for fence in self.geofences:
            try:
                distance = calculate_distance_km(node.latitude, node.longitude, fence.latitude, fence.longitude)
                # Check if distance calculation was valid
                if distance == float('inf'):
                     logger.warning(f"Invalid distance calculation for node {node_id} to fence '{fence.name}'. Skipping check.")
                     continue

                is_inside = distance <= fence.radius_km

                if is_inside:
                    currently_inside_fences.add(fence.name)
                    # Check if this is a new entry into this specific fence
                    if fence.name not in previously_inside_fences:
                        msg = f"{node_name} ({node_id}) entered geofence '{fence.name}' (dist {distance:.2f}km <= radius {fence.radius_km}km)."
                        logger.info(msg)
                        notifications.append(msg)
                # No need for 'else' here, exit detection is done below by comparing sets

            except Exception as e:
                logger.error(f"Error checking node {node_id} against fence '{fence.name}': {e}", exc_info=True)


        # Check for exits by comparing the previous set with the current set
        exited_fences = previously_inside_fences - currently_inside_fences
        for fence_name in exited_fences:
             # Find fence details again for logging distance (less efficient but clearer)
             exited_fence = next((f for f in self.geofences if f.name == fence_name), None)
             if exited_fence:
                 # Recalculate distance for the exit message (position might have changed slightly)
                 distance = calculate_distance_km(node.latitude, node.longitude, exited_fence.latitude, exited_fence.longitude)
                 # Ensure distance is valid before logging
                 dist_str = f"{distance:.2f}km" if distance != float('inf') else "unknown distance"
                 msg = f"{node_name} ({node_id}) exited geofence '{fence_name}' (dist {dist_str} > radius {exited_fence.radius_km}km)."
                 logger.info(msg)
                 notifications.append(msg)
             else: # Should not happen if internal state is correct
                 logger.warning(f"Node {node_id} exited unknown fence '{fence_name}' - state inconsistency?")


        # Update state for the node
        if currently_inside_fences:
            self._nodes_inside[node_id] = currently_inside_fences
        elif node_id in self._nodes_inside: # Was inside at least one fence, now inside none
            del self._nodes_inside[node_id] # Remove the entry for this node

        return notifications

    def get_nodes_inside_summary(self) -> Dict[str, List[str]]:
        """Returns a dictionary summarizing which nodes are in which fences {fence_name: [node_id,...]}."""
        summary: Dict[str, List[str]] = {fence.name: [] for fence in self.geofences}
        # Iterate through the current state
        for node_id, inside_fences_set in self._nodes_inside.items():
            for fence_name in inside_fences_set:
                 if fence_name in summary: # Should always be true if initialized correctly
                     summary[fence_name].append(node_id) # Store node ID
        return summary

    def reload_geofences(self):
        """Reloads geofences from the updated global config object."""
        # Assumes the global `config` object has already been updated
        # by the web handler after saving the file.
        self.geofences = config.geofences
        # Reset state as definitions might have changed drastically
        # Keep existing state? Or clear? Clearing is safer if fences change names/locations.
        # Let's clear it for simplicity. A node exiting an old fence won't be reported.
        self._nodes_inside.clear()
        logger.info(f"Reloaded {len(self.geofences)} geofences. Resetting internal geofence state.")

