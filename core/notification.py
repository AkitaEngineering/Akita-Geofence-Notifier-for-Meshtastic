# --- File: akita_geofence_notifier/akita_geofence_notifier/core/notification.py ---
import logging
import time
import math
from typing import Optional, Dict
from .config import config
from .meshtastic_utils import send_meshtastic_text, set_device_led, connection_status
from .models import NodeInfo # Use NodeInfo for context if needed

logger = logging.getLogger(__name__)

# Store last notification times to avoid spamming
# Key: unique event identifier (e.g., "geofence_enter_NodeID_FenceName", "stationary_NodeID")
# Value: timestamp
last_notification_time: Dict[str, float] = {}
NOTIFICATION_COOLDOWN = 60 # Seconds between identical notifications for the SAME event

class NotificationModule:
    """Handles sending text notifications and controlling LED feedback."""
    def __init__(self, meshtastic_interface):
        self.interface = meshtastic_interface
        logger.info("Notification module initialized.")

    def _can_notify(self, event_key: str) -> bool:
        """Checks if enough time has passed since the last notification for this specific event."""
        now = time.time()
        last_time = last_notification_time.get(event_key, 0)
        if now - last_time > NOTIFICATION_COOLDOWN:
            # Update last time only if we are actually sending notification
            # last_notification_time[event_key] = now # Moved to send_text_notification
            return True
        # logger.debug(f"Notification cooldown active for event: {event_key}")
        return False

    def send_text_notification(self, message: str, event_key: str, force: bool = False):
        """
        Sends a text notification via Meshtastic if cooldown allows.
        Requires a unique event_key for cooldown tracking.
        """
        if self._can_notify(event_key) or force:
             # Use the utility function which handles channel/PSK logic
             send_meshtastic_text(self.interface, message)
             # Update cooldown timer *after* successful send attempt
             last_notification_time[event_key] = time.time()
        else:
             logger.debug(f"Notification cooldown active for event: {event_key} - Message suppressed: {message}")

    def update_led_proximity(self, closest_node_distance_km: Optional[float]):
        """Updates the LED blink rate based on the closest node's distance."""
        if not config.enable_led_feedback:
             # Ensure LED is off if disabled (call set_device_led with off state)
             set_device_led(self.interface, state=False, frequency_hz=0)
             return

        if closest_node_distance_km is None or closest_node_distance_km == float('inf'):
            # No nodes nearby or no distance calculated, turn LED off
            set_device_led(self.interface, state=False, frequency_hz=0)
            # logger.debug("LED Off: No nodes nearby.")
        else:
             # Calculate frequency based on distance (inverse relationship)
             min_dist = max(0.001, config.led_min_distance_km) # Avoid division by zero, ensure positive
             max_freq = config.led_max_frequency_hz

             if max_freq <= 0: # Flashing disabled via frequency setting
                 set_device_led(self.interface, state=False, frequency_hz=0)
                 return

             # Clamp distance to minimum distance for max frequency calculation
             clamped_distance = max(min_dist, closest_node_distance_km)

             # Simple inverse scaling: freq = (min_dist / distance) * max_freq
             # This makes frequency decrease as distance increases.
             scale_factor = min_dist / clamped_distance
             frequency = scale_factor * max_freq

             # Clamp frequency to reasonable bounds (e.g., 0.1 Hz to max_freq)
             frequency = min(max_freq, max(0.1, frequency))

             # logger.debug(f"Setting LED freq based on distance {closest_node_distance_km:.3f}km -> {frequency:.2f}Hz")
             # Pass frequency to the utility function; state=None implies blinking if freq > 0
             set_device_led(self.interface, state=None, frequency_hz=frequency)

