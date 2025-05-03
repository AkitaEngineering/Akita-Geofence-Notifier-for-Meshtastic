# --- File: akita_geofence_notifier/akita_geofence_notifier/core/stationary.py ---
import time
import logging
from collections import deque, defaultdict
from typing import Dict, List, Tuple, Optional, Deque
from .config import config
from .distance import calculate_distance_km
from .models import NodeInfo

logger = logging.getLogger(__name__)

# Structure to store recent locations: {node_id: deque([(timestamp, lat, lon), ...])}
# Use deque for efficient addition/removal from both ends
# Maxlen limits memory usage per node. Adjust based on typical update frequency and time threshold.
# maxlen should be roughly (stationary_time_threshold / typical_update_interval) * safety_factor
NODE_HISTORY_MAXLEN = 50 # Example: Keep last 50 points
node_location_history: Dict[str, Deque[Tuple[float, float, float]]] = defaultdict(lambda: deque(maxlen=NODE_HISTORY_MAXLEN))

# Track if node is currently considered stationary {node_id: bool}
node_stationary_state: Dict[str, bool] = {}

class StationaryModule:
    """Checks if nodes have remained stationary based on location history."""
    def __init__(self):
        # Store thresholds locally, can be updated by config reload
        self.time_threshold = config.stationary_time_threshold
        self.distance_threshold = config.stationary_distance_threshold
        logger.info(f"Stationary check configured: Time={self.time_threshold}s, Distance={self.distance_threshold*1000:.0f}m")

    def update_node_location(self, node: NodeInfo):
        """Adds the current location of a node to its history if valid."""
        if node.latitude is not None and node.longitude is not None:
            # Use system time (last_heard) as the primary timestamp for consistency checking interval
            # GPS time (position_time) might be intermittent or inaccurate if RTC isn't set.
            timestamp = node.last_heard or time.time() # Fallback to current time if last_heard is missing

            history = node_location_history[node.node_id]

            # Avoid adding duplicate locations if timestamp and coords are identical to the last one
            if not history or (timestamp > history[-1][0] and \
                               (node.latitude != history[-1][1] or node.longitude != history[-1][2])):
                 history.append((timestamp, node.latitude, node.longitude))
                 # logger.debug(f"Added location for {node.node_id} at {timestamp:.0f}: ({node.latitude:.5f}, {node.longitude:.5f}) | History size: {len(history)}")


    def check_node_stationary(self, node: NodeInfo) -> Tuple[bool, Optional[str]]:
        """
        Checks if a node has been stationary for the configured threshold.
        Compares earliest and latest points within the time window.
        Returns (is_now_stationary, notification_message).
        Notification message is generated only on state change (stationary -> moving or vice versa).
        """
        node_id = node.node_id
        node_name = node.name or f"Node {node_id[-4:]}"
        history = node_location_history.get(node_id)
        was_stationary = node_stationary_state.get(node_id, False)
        is_now_stationary = False
        notification = None

        # Need at least two points in history to check for movement
        if not history or len(history) < 2:
            # logger.debug(f"Insufficient history for stationary check on {node_id}")
            # If it was stationary but history cleared/insufficient, mark as not stationary
            if was_stationary:
                 node_stationary_state[node_id] = False
                 # Optionally notify that status is unknown now? No, too noisy.
            return False, None # Cannot determine

        # Get the timestamp of the most recent update
        last_time, last_lat, last_lon = history[-1]

        # Find the earliest point in the history that is still within the time threshold window
        # looking backwards from the last point's time.
        earliest_point_in_window = None
        for i in range(len(history) - 1, -1, -1):
            entry_time, entry_lat, entry_lon = history[i]
            if last_time - entry_time >= self.time_threshold:
                # This point is at or before the start of the time window
                earliest_point_in_window = (entry_time, entry_lat, entry_lon)
                break # Found the start of our relevant window

        if earliest_point_in_window is None:
             # Not enough time duration covered by the history yet
             # logger.debug(f"History timespan too short for stationary check on {node_id}")
             if was_stationary: # Was stationary, but history doesn't cover threshold anymore
                  node_stationary_state[node_id] = False
             return False, None

        # Calculate distance between the earliest point in the window and the latest point
        start_time_in_window, start_lat, start_lon = earliest_point_in_window
        distance_moved = calculate_distance_km(start_lat, start_lon, last_lat, last_lon)
        time_span_checked = last_time - start_time_in_window

        # Check if distance calculation was valid
        if distance_moved == float('inf'):
            logger.warning(f"Invalid distance calculation during stationary check for {node_id}. Skipping check.")
            # If it was stationary, mark as not stationary as we can't confirm
            if was_stationary:
                node_stationary_state[node_id] = False
            return False, None

        # logger.debug(f"Stationary check for {node_id}: Timespan={time_span_checked:.0f}s, Dist Moved={distance_moved*1000:.1f}m")

        # Check if distance moved is within the threshold
        if distance_moved <= self.distance_threshold:
            is_now_stationary = True
            # Generate notification only if state changed to stationary
            if not was_stationary:
                notification = f"{node_name} ({node_id}) has been stationary for >{self.time_threshold}s (moved {distance_moved*1000:.1f}m)."
                logger.info(notification)
                node_stationary_state[node_id] = True
        else:
            # Moved more than threshold distance
            is_now_stationary = False
            # Generate notification only if state changed from stationary to moving
            if was_stationary:
                notification = f"{node_name} ({node_id}) is moving again (moved {distance_moved*1000:.1f}m in {time_span_checked:.0f}s)."
                logger.info(notification)
                node_stationary_state[node_id] = False

        return is_now_stationary, notification

    def get_stationary_nodes(self) -> List[str]:
        """Returns a list of node IDs currently considered stationary."""
        # Return nodes currently marked as stationary in the state dictionary
        return [node_id for node_id, state in node_stationary_state.items() if state]

    def cleanup_stale_nodes(self, stale_threshold_seconds: int = 3600):
         """Removes history and state for nodes not heard from recently."""
         now = time.time()
         stale_nodes = []
         # Check last update time in history (requires history not to be empty)
         for node_id, history in list(node_location_history.items()): # Iterate over copy of keys
              if history and now - history[-1][0] > stale_threshold_seconds:
                   stale_nodes.append(node_id)

         if stale_nodes:
              logger.info(f"Cleaning up stale stationary data for nodes: {stale_nodes}")
              for node_id in stale_nodes:
                   node_location_history.pop(node_id, None)
                   node_stationary_state.pop(node_id, None)

