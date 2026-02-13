# --- File: akita_geofence_notifier/akita_geofence_notifier/core/meshtastic_utils.py ---
import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
import meshtastic.util
import time
import logging
import os
import threading
from pubsub import pub # Meshtastic uses pubsub for events
from typing import Dict, Optional, Callable, Any, Union
from .config import config
from .models import NodeInfo # Import your NodeInfo dataclass/model

logger = logging.getLogger(__name__)

# --- Shared State ---
# Use locks for thread safety when accessing/modifying this from multiple threads
# (e.g., meshtastic callback thread, main check thread, web server thread)
node_db: Dict[str, NodeInfo] = {}
node_db_lock = threading.Lock()

my_node_info: Optional[NodeInfo] = None
my_node_info_lock = threading.Lock()

connection_status = {"connected": False, "type": None, "port_or_host": None, "error": None}
connection_status_lock = threading.Lock()

# --- Callbacks ---
def on_receive(packet, interface): # pylint: disable=unused-argument
    """Callback for received packets."""
    # logger.debug(f"Received packet: {packet}") # Very verbose

    # Extract node ID safely
    node_id = packet.get("fromId")
    if not node_id:
        # logger.debug("Received packet with no 'fromId'")
        return

    # Process based on portnum
    portnum_str = packet.get("decoded", {}).get("portnum") # Portnum as string (e.g., "NODEINFO_APP")

    # Update last heard time regardless of packet type
    with node_db_lock:
        if node_id not in node_db:
            # If it's a new node, create a basic entry
            node_db[node_id] = NodeInfo(node_id=node_id)
            logger.info(f"Discovered new node: {node_id}")

        # Update common fields
        node_db[node_id].last_heard = time.time()
        if 'rxSnr' in packet:
             node_db[node_id].snr = packet.get('rxSnr')
        if 'rxRssi' in packet:
             # Store RSSI (dBm) if present
             try:
                 node_db[node_id].rssi = float(packet.get('rxRssi'))
             except (TypeError, ValueError):
                 node_db[node_id].rssi = None
        # Battery level might be in NODEINFO_APP or TELEMETRY_APP
        # Update it wherever we see it

    # Handle specific packet types based on string portnum
    if portnum_str == "NODEINFO_APP":
        process_nodeinfo_packet(packet, node_id)
    elif portnum_str == "POSITION_APP":
        process_position_packet(packet, node_id)
    elif portnum_str == "TELEMETRY_APP":
         process_telemetry_packet(packet, node_id)
    # Add handlers for other PortNum strings if needed (e.g., "TEXT_MESSAGE_APP", "ROUTING_APP")
    # else:
    #     logger.debug(f"Received unhandled portnum '{portnum_str}' from {node_id}")


def on_connection(interface, topic=pub.AUTO_TOPIC): # pylint: disable=unused-argument
    """Callback for connection status changes."""
    topic_str = pub.AUTO_TOPIC.getName() # Get the string representation of the topic
    logger.debug(f"Meshtastic connection event: {topic_str}")
    status_parts = topic_str.split('.')
    status = status_parts[-1] if status_parts else "unknown" # e.g., established, lost

    with connection_status_lock:
        if status == "established":
            connection_status["connected"] = True
            connection_status["error"] = None
            # Extract connection details
            if isinstance(interface, meshtastic.serial_interface.SerialInterface):
                 connection_status["type"] = "Serial"
                 try: connection_status["port_or_host"] = interface.devPath
                 except AttributeError: connection_status["port_or_host"] = "Unknown Serial"
            elif isinstance(interface, meshtastic.tcp_interface.TCPInterface):
                 connection_status["type"] = "TCP"
                 try: connection_status["port_or_host"] = interface.hostname
                 except AttributeError: connection_status["port_or_host"] = "Unknown TCP"
            else:
                 connection_status["type"] = "Unknown"
                 connection_status["port_or_host"] = None

            logger.info(f"Meshtastic connection established ({connection_status['type']}: {connection_status.get('port_or_host', '?')})")
            # Update local node info immediately on connection
            update_my_node_info(interface)
            # Request node DB from mesh after connection? Optional.
            # interface.requestNodeDb() # Might flood network initially
        else: # lost, closing, etc.
             connection_status["connected"] = False
             # Don't clear type/port on temporary loss? Maybe. Let's clear.
             # connection_status["type"] = None
             # connection_status["port_or_host"] = None
             connection_status["error"] = status # Store the reason if known (e.g., 'lost')
             logger.warning(f"Meshtastic connection status changed: {status}")
             # Clear local node info when disconnected
             with my_node_info_lock:
                 global my_node_info
                 my_node_info = None


def process_nodeinfo_packet(packet, node_id):
    """Process received nodeinfo packets (USER packet)."""
    user_data = packet.get("decoded", {}).get("user", {})
    if not user_data: return

    long_name = user_data.get("longName", f"Node {node_id[-4:]}") # Use fallback name
    short_name = user_data.get("shortName", "?")
    hw_model_enum = user_data.get("hwModel") # This is an enum value
    bat_level = user_data.get("batteryLevel") # Battery level might be here too

    try:
        # Attempt to convert hwModel enum to string name using meshtastic library utils
        hw_model_str = meshtastic.util.hwModelToString(hw_model_enum)
    except Exception: # Catch potential errors if enum is unknown
        hw_model_str = "Unknown HW"

    with node_db_lock:
        # Node entry should exist from on_receive
        if node_id in node_db:
            node_db[node_id].name = long_name
            node_db[node_id].short_name = short_name
            node_db[node_id].hw_model = hw_model_str
            if bat_level is not None: # Update battery level if present
                 node_db[node_id].battery_level = int(bat_level)
            # last_heard updated in on_receive
            # logger.debug(f"Updated NodeInfo for {long_name} ({node_id})")
        else:
            # This case should ideally not happen if on_receive works correctly
             logger.warning(f"NodeInfo packet received for unknown node {node_id}. Creating entry.")
             node_db[node_id] = NodeInfo(
                 node_id=node_id,
                 name=long_name,
                 short_name=short_name,
                 hw_model=hw_model_str,
                 battery_level=int(bat_level) if bat_level is not None else None,
                 last_heard=time.time() # Set last heard time here too
             )


def process_position_packet(packet, node_id):
    """Process received position packets."""
    pos_data = packet.get("decoded", {}).get("position", {})
    if not pos_data: return

    lat = pos_data.get("latitude")
    lon = pos_data.get("longitude")
    alt = pos_data.get("altitude")
    timestamp = packet.get("decoded", {}).get("time") # Position timestamp (if device has RTC)

    # Meshtastic often sends 0,0 or large invalid numbers before a real fix
    # Check for validity (basic range check)
    is_valid_pos = False
    lat_f, lon_f = None, None # Initialize as None
    if lat is not None and lon is not None:
         try:
              lat_f_temp, lon_f_temp = float(lat), float(lon)
              if -90 <= lat_f_temp <= 90 and -180 <= lon_f_temp <= 180:
                   # Avoid position 0,0 unless specifically allowed/expected
                   if lat_f_temp != 0.0 or lon_f_temp != 0.0:
                        is_valid_pos = True
                        lat_f, lon_f = lat_f_temp, lon_f_temp # Assign only if valid
         except (ValueError, TypeError):
              pass # Invalid number format

    if not is_valid_pos:
        # logger.debug(f"Ignoring invalid position for {node_id}: Lat={lat}, Lon={lon}")
        # Clear position in DB if it becomes invalid? Maybe not, keep last known good.
        return

    with node_db_lock:
        # Node entry should exist from on_receive
        if node_id in node_db:
            node_db[node_id].latitude = lat_f
            node_db[node_id].longitude = lon_f
            node_db[node_id].altitude = int(alt) if alt is not None else None
            node_db[node_id].position_time = int(timestamp) if timestamp is not None else None
            # last_heard updated in on_receive
            # logger.debug(f"Updated position for {node_db[node_id].name} ({node_id}): Lat={lat_f:.5f}, Lon={lon_f:.5f}")
        else:
            # This case should ideally not happen if on_receive works correctly
            logger.warning(f"Position packet received for unknown node {node_id}. Creating entry.")
            node_db[node_id] = NodeInfo(
                node_id=node_id,
                latitude=lat_f,
                longitude=lon_f,
                altitude=int(alt) if alt is not None else None,
                position_time=int(timestamp) if timestamp is not None else None,
                last_heard=time.time() # Set last heard time
            )


def process_telemetry_packet(packet, node_id):
    """Process received telemetry packets (DeviceMetrics)."""
    metrics = packet.get("decoded", {}).get("deviceMetrics", {})
    if not metrics: return

    bat_level = metrics.get("batteryLevel")
    voltage = metrics.get("voltage")
    # air_util = metrics.get("airUtilTx")
    # uptime = metrics.get("uptimeSeconds")

    with node_db_lock:
         # Node entry should exist from on_receive
        if node_id in node_db:
            if bat_level is not None:
                node_db[node_id].battery_level = int(bat_level)
            # Store other metrics if needed (e.g., voltage)
            # logger.debug(f"Updated telemetry for {node_db[node_id].name}: Bat={bat_level}%, V={voltage:.2f}")
        # else: logger.warning(f"Telemetry packet received for unknown node {node_id}.")


def update_my_node_info(interface: meshtastic.MeshInterface):
     """Fetches and updates the local node's information."""
     try:
        # Ensure interface and myInfo are available
        # myInfo might take a moment to populate after connection
        retries = 3
        while retries > 0 and not interface.myInfo:
             logger.debug("Waiting for myInfo to populate...")
             time.sleep(1)
             retries -= 1

        if interface.myInfo:
             my_node_id_int = interface.myInfo.my_node_num # Integer node number
             my_node_id_str = f"!{my_node_id_int:08x}" # Standard hex format with '!'

             try:
                 # Attempt to convert hwModel enum to string name using meshtastic library utils
                 hw_model_str = meshtastic.util.hwModelToString(interface.myInfo.hw_model)
             except Exception: # Catch potential errors if enum is unknown
                 hw_model_str = "Unknown HW"

             with my_node_info_lock:
                global my_node_info
                my_node_info = NodeInfo(
                    node_id=my_node_id_str,
                    name=interface.myInfo.long_name or f"Node {my_node_id_str[-4:]}", # Use long name or fallback
                    short_name=interface.myInfo.short_name or "?",
                    hw_model=hw_model_str,
                    # Local node position is updated by GPS module sending to mesh
                )
                logger.info(f"Updated local node info: {my_node_info.name} ({my_node_info.node_id}), HW: {my_node_info.hw_model}")

                # Update node_db with local info as well
                with node_db_lock:
                    # Ensure local node exists in DB and update its details
                    if my_node_info.node_id not in node_db:
                         node_db[my_node_info.node_id] = NodeInfo(node_id=my_node_info.node_id) # Create basic entry if missing

                    node_db[my_node_info.node_id].name = my_node_info.name
                    node_db[my_node_info.node_id].short_name = my_node_info.short_name
                    node_db[my_node_info.node_id].hw_model = my_node_info.hw_model
                    node_db[my_node_info.node_id].last_heard = time.time() # Mark as heard recently

        else:
            logger.warning("Could not get local node info (myInfo is not available after retries)")
            with my_node_info_lock:
                 my_node_info = None # Ensure it's None if we failed

     except Exception as e:
         logger.error(f"Error fetching local node info: {e}", exc_info=True)
         with my_node_info_lock:
              my_node_info = None # Ensure it's None on error


def connect_meshtastic() -> Optional[meshtastic.MeshInterface]:
    """Attempts to connect to a Meshtastic device (Serial or TCP)."""
    logger.info("Attempting to connect to Meshtastic device...")
    interface = None

    # --- Subscribe to connection events EARLY ---
    # This helps capture the 'established' event even if connection happens quickly
    pub.subscribe(on_connection, "meshtastic.connection")

    # --- Try Serial Connection ---
    try:
        logger.info("Trying Serial connection...")
        # Specify device path if needed, otherwise it auto-detects
        # interface = meshtastic.serial_interface.SerialInterface(devPath='/dev/ttyACM0')
        interface = meshtastic.serial_interface.SerialInterface()
        # Wait briefly for potential connection callback and myInfo population
        time.sleep(2)
        with connection_status_lock:
             # Check if the on_connection callback marked it as connected
             if connection_status["connected"] and connection_status["type"] == "Serial":
                  logger.info(f"Successfully connected via Serial to {connection_status.get('port_or_host', '?')}")
                  pub.subscribe(on_receive, "meshtastic.receive") # Subscribe to data packets
                  return interface
             else:
                  logger.warning("Serial connection attempt failed or timed out waiting for connection event.")
                  if interface: interface.close()
                  interface = None # Ensure interface is None if connection failed

    except meshtastic.MeshtasticError as e:
         logger.warning(f"Meshtastic Serial connection error: {e}. Trying TCP...")
         if interface: interface.close()
         interface = None
    except Exception as e: # Catch broader errors like serial port access denied
         logger.warning(f"Non-Meshtastic Serial error: {e}. Trying TCP...")
         if interface: interface.close()
         interface = None

    # --- Try TCP Connection (if Serial failed) ---
    if interface is None:
        try:
            # MESH_HOST env var can override default hostname lookup
            tcp_host = os.environ.get("MESH_HOST")
            log_msg = "Trying TCP connection"
            if tcp_host:
                 log_msg += f" to host: {tcp_host}"
            else:
                 log_msg += " (using default host/port or MESH_HOST/MESH_PORT env vars)..."
            logger.info(log_msg)

            # Specify hostname if needed: interface = meshtastic.tcp_interface.TCPInterface(hostname="meshtastic.local")
            interface = meshtastic.tcp_interface.TCPInterface(hostname=tcp_host) # Pass hostname if set
            # TCPInterface connects automatically, wait for potential connection callback
            time.sleep(2) # Allow time for connection attempt and callback
            with connection_status_lock:
                if connection_status["connected"] and connection_status["type"] == "TCP":
                    logger.info(f"Successfully connected via TCP to {connection_status['port_or_host']}")
                    pub.subscribe(on_receive, "meshtastic.receive") # Subscribe to data packets
                    return interface
                else:
                     logger.warning("TCP connection attempt failed or timed out waiting for connection event.")
                     if interface: interface.close()
                     interface = None

        except meshtastic.MeshtasticError as e:
             logger.error(f"Meshtastic TCP connection error: {e}")
             if interface: interface.close()
             interface = None
        except Exception as e:
             logger.error(f"Unexpected TCP connection error: {e}")
             if interface: interface.close()
             interface = None

    # --- Final Check ---
    if interface is None:
         logger.error("Failed to connect to Meshtastic device via Serial or TCP.")
         # Unsubscribe from connection events if we failed completely
         try: pub.unsubscribe(on_connection, "meshtastic.connection")
         except Exception as e:
             logger.debug(f"Failed to unsubscribe from connection events: {e}")
         return None
    else:
         # Should not be reached if logic is correct, but as safety:
         return interface


def send_meshtastic_text(interface: meshtastic.MeshInterface, message: str, destinationId: str = "!all", wantAck=False):
    """Sends a text message over Meshtastic, handling private channel."""
    if not interface or not connection_status.get("connected"):
        logger.warning("Cannot send message, Meshtastic not connected.")
        return

    channel_index = 0 # Default Primary channel (index 0)
    is_private = False

    # Check if a valid private PSK is configured
    # Treat common defaults/placeholders as "use primary channel"
    private_psk_setting = config.private_channel_psk or "" # Treat None as empty string
    if private_psk_setting.strip() and private_psk_setting.lower() not in ["", "changeme", "none", "default", "primary"]:
        try:
            # Meshtastic typically uses channel 1 for the first secondary channel
            # A more robust method would check interface.channels for matching settings if available
            if len(interface.channels) > 1:
                 channel_index = 1 # Assume secondary channel is index 1
                 is_private = True
                 logger.debug(f"Sending on private channel index {channel_index}")
                 # Note: The PSK itself is handled by the interface configuration,
                 # we just need to specify the channel index.
            else:
                 logger.warning("Private PSK configured, but no secondary channel found on device. Sending on Primary channel.")

        except Exception as e:
             logger.error(f"Error determining private channel index: {e}. Sending on Primary channel.")
             channel_index = 0
    else:
        logger.debug("Sending on primary channel index 0.")


    try:
        logger.info(f"Sending{' private' if is_private else ''} message via channel {channel_index} to {destinationId}: {message}")
        interface.sendText(
            text=message,
            destinationId=destinationId,
            channelIndex=channel_index,
            wantAck=wantAck
        )
    except meshtastic.MeshtasticError as e:
         logger.error(f"Meshtastic error sending message: {e}")
    except Exception as e:
        logger.error(f"Failed to send Meshtastic message: {e}", exc_info=True)


def set_device_led(interface: meshtastic.MeshInterface, state: bool = None, frequency_hz: float = 0):
     """
     Controls the device LED using setNodeConfig (Adapt KEY and VALUES).
     NOTE: This is experimental and requires device-specific KEY/VALUE research.
           The specific key and values needed vary greatly between devices and firmware versions.
           Find the correct key/values using 'meshtastic --info', 'meshtastic --getall',
           or by consulting Meshtastic documentation/community for your specific hardware.
     """
     if not config.enable_led_feedback:
          # Try to ensure LED is off if feature is disabled; attempt several common keys (best-effort)
          if interface and connection_status.get("connected"):
               for off_key in ("device.led_mode", "led.mode", "led.blink", "led.brightness"):
                    try:
                         # brightness keys accept numeric level (0 = off); mode-like keys commonly use 0=off
                         interface.setNodeConfig(off_key, 0)
                         logger.debug(f"Disabling LED feedback via {off_key}=0")
                         break
                    except Exception:
                         # Try next candidate key
                         continue
          else:
               logger.debug("LED feedback disabled in config; no Meshtastic interface available to explicitly turn off LED.")
          return

     if not interface or not connection_status.get("connected"):
          logger.warning("Cannot set LED, Meshtastic not connected.")
          return

     # Candidate keys to try (best-effort across different devices/firmware).
     candidate_keys = [
         "device.led_mode",
         "led.mode",
         "led.blink",
         "led.brightness",
     ]

     # Determine logical target mode from provided inputs
     if frequency_hz > 1.5:
         target_mode = "fast"
     elif frequency_hz > 0.2:
         target_mode = "slow"
     elif state is True:
         target_mode = "on"
     else:
         target_mode = "off"

     def _value_for_key(key_name: str, mode: str):
         """Return a sensible value for the given key and logical mode (best-effort)."""
         if "brightness" in key_name:
             return 0 if mode == "off" else (255 if mode == "on" else 128)
         mapping = {"off": 0, "on": 1, "slow": 2, "fast": 3}
         return mapping.get(mode, 0)

     # Try to read current node config (if interface exposes it) to avoid unnecessary writes
     current_conf = None
     get_conf = getattr(interface, "getNodeConfig", None)
     if callable(get_conf):
         try:
             current_conf = get_conf()
         except Exception:
             current_conf = None

     set_success = False
     last_error = None
     for key in candidate_keys:
         val = _value_for_key(key, target_mode)
         try:
             if isinstance(current_conf, dict) and key in current_conf and current_conf.get(key) == val:
                 logger.debug(f"LED config '{key}' already set to {val}; skipping write.")
                 set_success = True
                 break

             interface.setNodeConfig(key, val)
             logger.info(f"LED config set: {key}={val}")
             set_success = True
             break
         except AttributeError:
             # Interface doesn't support setNodeConfig
             raise AttributeError("Meshtastic interface does not have 'setNodeConfig' method. Cannot control LED.")
         except Exception as e:
             last_error = e
             logger.debug(f"Attempt to set LED config '{key}' failed: {e}")
             continue

     if not set_success:
         if last_error:
             logger.error(f"Failed to set LED configuration (last error): {last_error}")
         else:
             logger.warning("Could not find a suitable LED config key on this device/firmware. LED control not applied.")


def set_gps_location_on_mesh(interface: meshtastic.MeshInterface, lat: float, lon: float, alt: Optional[int] = None):
     """Sends the device's own GPS location to the mesh using sendPosition."""
     if not interface or not connection_status.get("connected"):
          logger.warning("Cannot send GPS location, Meshtastic not connected.")
          return
     if lat is None or lon is None:
         logger.warning("Cannot send GPS location, invalid coordinates (None).")
         return

     try:
          # Basic validation
          lat_f, lon_f = float(lat), float(lon)
          if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
               logger.warning(f"Cannot send GPS location, invalid coordinates: Lat={lat_f}, Lon={lon_f}")
               return

          # Altitude handling
          alt_int: Optional[int] = None
          if alt is not None:
               try:
                    alt_int = int(alt)
               except (ValueError, TypeError):
                    logger.warning(f"Invalid altitude value '{alt}', sending without altitude.")

          logger.info(f"Sending GPS location to mesh: Lat={lat_f:.5f}, Lon={lon_f:.5f}" + (f", Alt={alt_int}m" if alt_int is not None else ""))
          # Use sendPosition (more modern, includes altitude etc.)
          interface.sendPosition(latitude=lat_f, longitude=lon_f, altitude=alt_int)
          # Older method (might still work):
          # interface.setGPSLocation(latitude=lat_f, longitude=lon_f)

     except AttributeError:
          logger.error("Meshtastic interface lacks sendPosition method.")
     except meshtastic.MeshtasticError as e:
          logger.error(f"Meshtastic error sending position: {e}")
     except ValueError:
           logger.error(f"Cannot send GPS location, invalid number format: Lat={lat}, Lon={lon}")
     except Exception as e:
          logger.error(f"Failed to send GPS location: {e}", exc_info=True)

