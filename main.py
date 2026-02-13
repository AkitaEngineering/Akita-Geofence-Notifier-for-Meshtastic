# --- File: akita_geofence_notifier/akita_geofence_notifier/main.py ---
import threading
import time
import logging
import sys
import signal
import os
import meshtastic
from queue import Queue, Empty
from typing import Optional, List, Dict

# --- Early setup for logging ---
# Define logger format
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Get root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Set root level (can be overridden by handlers)

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO) # Set console level (e.g., INFO)
root_logger.addHandler(console_handler)

# Optional File Handler (Uncomment to enable file logging)
# try:
#     # Ensure logs directory exists if logging to a subfolder
#     # os.makedirs("logs", exist_ok=True)
#     # file_handler = logging.FileHandler("logs/geofence_notifier.log", mode='a') # Append mode
#     file_handler = logging.FileHandler("geofence_notifier.log", mode='a') # Append mode
#     file_handler.setFormatter(log_formatter)
#     file_handler.setLevel(logging.DEBUG) # Log DEBUG level and above to file
#     root_logger.addHandler(file_handler)
#     logging.info("File logging enabled to geofence_notifier.log")
# except Exception as e:
#     logging.error(f"Failed to set up file logging: {e}")

logger = logging.getLogger(__name__) # Logger for this module

# --- Import core modules AFTER logging is set up ---
# Ensure __init__ is imported to get version if needed elsewhere
from . import __version__
# Import config first as other modules rely on the global 'config' object
from .core.config import config, AppConfig, CONFIG_FILENAME
# Import other core modules
from .core.gps import GPSModule
from .core.meshtastic_utils import (
    connect_meshtastic, node_db, node_db_lock, my_node_info, my_node_info_lock,
    set_gps_location_on_mesh, connection_status, connection_status_lock,
    send_meshtastic_text # Import send function directly if needed elsewhere
)
from .core.geofence import GeofenceModule
from .core.stationary import StationaryModule, node_stationary_state # Import state if needed
from .core.notification import NotificationModule
from .core.distance import calculate_distance_km
from .core.models import NodeInfo
# Import web app starter function
from .web.app import start_web_app

# --- Global state / Shared resources ---
meshtastic_interface: Optional[meshtastic.MeshInterface] = None
gps_module: Optional[GPSModule] = None
geofence_module: Optional[GeofenceModule] = None
stationary_module: Optional[StationaryModule] = None
notification_module: Optional[NotificationModule] = None

stop_event = threading.Event() # Global stop signal for threads
config_reload_event = threading.Event() # Event for signaling config reload
config_lock = threading.Lock() # Lock for safely accessing/updating global config object
notification_queue = Queue() # Queue for passing notifications to web/other parts
background_threads: List[threading.Thread] = [] # Keep track of threads


def gps_update_loop():
    """Thread loop to read GPS and send updates to mesh."""
    global gps_module # Access the global module instance

    if not gps_module:
        logger.error("GPS Update Loop cannot start: GPSModule not initialized.")
        return

    logger.info("Starting GPS update loop...")
    # Note: Changing port/baud dynamically is complex and not implemented here.
    # These are read once at GPSModule initialization.
    current_gps_update_interval = config.gps_update_interval

    # Start GPS reading *after* potentially getting initial interval
    gps_module.start_reading()

    while not stop_event.is_set():
         # Check for config reload signal (mainly for interval)
        if config_reload_event.is_set():
            logger.debug("Config reload event detected in gps_update_loop.")
            with config_lock:
                # Check if interval actually changed
                new_interval = config.gps_update_interval
                if new_interval != current_gps_update_interval:
                     current_gps_update_interval = new_interval
                     logger.info(f"GPS update interval reloaded to {current_gps_update_interval}s.")
            # Don't clear the event here; let the web thread manage it
            # config_reload_event.clear() # Let web thread clear after a delay

        try:
            latitude, longitude = gps_module.get_location()
            altitude = None # Altitude not typically provided by basic NMEA GGA parsing in current GPSModule

            if latitude is not None and longitude is not None:
                 # Update local node info in the database (if local node is known)
                 with my_node_info_lock:
                      local_node_id = my_node_info.node_id if my_node_info else None

                 if local_node_id:
                      with node_db_lock:
                           if local_node_id in node_db:
                                node_db[local_node_id].latitude = latitude
                                node_db[local_node_id].longitude = longitude
                                node_db[local_node_id].altitude = altitude # Store altitude if available
                                node_db[local_node_id].position_time = int(time.time()) # Use system time for own updates
                                # logger.debug(f"Updated local node position in DB: {latitude:.5f}, {longitude:.5f}")
                           # else: logger.warning("Local node ID not found in node_db during GPS update.")

                 # Send location to mesh
                 if meshtastic_interface and connection_status.get("connected"):
                      set_gps_location_on_mesh(meshtastic_interface, latitude, longitude, altitude)

            # else: logger.debug("Waiting for GPS fix...") # Reduce log noise if no fix yet

            # Wait using the potentially updated interval
            stop_event.wait(current_gps_update_interval)

        except Exception as e:
            logger.error(f"Error in GPS update loop: {e}", exc_info=True)
            # Avoid tight loop on error
            stop_event.wait(15) # Wait longer after an error

    # Cleanup when loop exits
    if gps_module:
        gps_module.stop_reading()
    logger.info("GPS update loop finished.")


def periodic_check_loop():
    """Thread loop for periodic checks (distance, geofence, stationary)."""
    global geofence_module, stationary_module, notification_module # Access global modules

    if not all([geofence_module, stationary_module, notification_module]):
         logger.error("Periodic Check Loop cannot start: Core modules not initialized.")
         return

    logger.info("Starting periodic check loop...")
    time.sleep(5) # Allow some time for initial node discovery

    # Store local copies of intervals/thresholds that might change
    current_check_interval = config.check_interval
    # Ensure stationary module thresholds are initialized from config
    if stationary_module:
        stationary_module.time_threshold = config.stationary_time_threshold
        stationary_module.distance_threshold = config.stationary_distance_threshold
    last_stale_cleanup_time = time.time()

    while not stop_event.is_set():
        # --- Config Reload Check ---
        if config_reload_event.is_set():
            logger.debug("Config reload event detected in periodic_check_loop.")
            try:
                with config_lock: # Ensure reading consistent config state
                    # Update local copies of parameters used in this loop
                    new_check_interval = config.check_interval
                    new_stat_time = config.stationary_time_threshold
                    new_stat_dist = config.stationary_distance_threshold

                    if new_check_interval != current_check_interval:
                        current_check_interval = new_check_interval
                        logger.info(f"Check interval reloaded to {current_check_interval}s.")
                    # Update stationary module thresholds directly
                    if stationary_module:
                        if new_stat_time != stationary_module.time_threshold:
                             stationary_module.time_threshold = new_stat_time
                             logger.info(f"Stationary time threshold reloaded to {stationary_module.time_threshold}s.")
                        if new_stat_dist != stationary_module.distance_threshold:
                             stationary_module.distance_threshold = new_stat_dist
                             logger.info(f"Stationary distance threshold reloaded to {stationary_module.distance_threshold*1000:.0f}m.")

                    # Reload geofences
                    if geofence_module:
                        geofence_module.reload_geofences() # Reloads from global config

                    # Reload notification module settings if needed (e.g., LED params)
                    # Currently LED params are read directly from config inside update_led_proximity
                    logger.info("Checked for updated settings (intervals, thresholds, geofences, LED params).")

                # Let web thread clear the event
                # config_reload_event.clear()

            except Exception as e:
                logger.error(f"Error reloading configuration in periodic_check_loop: {e}", exc_info=True)
            # End of reload handling

        # --- Main Check Logic ---
        try:
            if not meshtastic_interface or not connection_status.get("connected"):
                 logger.warning("Check loop waiting for Meshtastic connection...")
                 stop_event.wait(current_check_interval) # Wait before checking again
                 continue

            # Get local position (if available) for distance calcs
            local_lat, local_lon = (None, None)
            if gps_module: # Check if GPS module exists
                 local_lat, local_lon = gps_module.get_location()

            closest_node_dist = float('inf')

            # Get a copy of nodes to iterate over to avoid holding lock too long
            with node_db_lock:
                # Filter out the local node from the list to check
                my_id = my_node_info.node_id if my_node_info else None
                nodes_to_check = [node for node in node_db.values() if node.node_id != my_id]

            # Process each remote node
            for node in nodes_to_check:
                # --- Update history/modules ---
                if stationary_module:
                    stationary_module.update_node_location(node) # Update history first

                # --- Geofence Check ---
                if geofence_module:
                    fence_notifications = geofence_module.check_node(node)
                    for i, msg in enumerate(fence_notifications):
                        # Create a unique event key for cooldown
                        event_type = "geofence_enter" if "entered" in msg else "geofence_exit"
                        # Try to extract fence name more reliably
                        parts = msg.split("'")
                        fence_name_guess = parts[1] if len(parts) > 1 else f"unknown{i}"
                        event_key = f"{event_type}_{node.node_id}_{fence_name_guess}"
                        if notification_module:
                            notification_module.send_text_notification(msg, event_key)
                        notification_queue.put({"type": "geofence", "message": msg, "node_id": node.node_id}) # Send to web queue

                # --- Stationary Check ---
                if stationary_module:
                    is_stationary, stationary_msg = stationary_module.check_node_stationary(node)
                    if stationary_msg:
                        event_type = "stationary_start" if "has been stationary" in stationary_msg else "stationary_stop"
                        event_key = f"{event_type}_{node.node_id}"
                        if notification_module:
                            notification_module.send_text_notification(stationary_msg, event_key)
                        notification_queue.put({"type": "stationary", "message": stationary_msg, "node_id": node.node_id}) # Send to web queue

                # --- Distance Calculation (for LED) ---
                if local_lat is not None and local_lon is not None:
                    # Calculate distance from local node to this remote node
                    distance = calculate_distance_km(local_lat, local_lon, node.latitude, node.longitude)
                    if distance < closest_node_dist:
                        closest_node_dist = distance
                    # logger.debug(f"Distance to {node.name}: {distance:.2f} km")

            # --- LED Proximity Update ---
            # Update LED based on the *closest* node found in this cycle
            if notification_module:
                notification_module.update_led_proximity(closest_node_dist if closest_node_dist != float('inf') else None)

            # --- Periodic Cleanup ---
            # Cleanup stale node data occasionally (e.g., every hour)
            now = time.time()
            if stationary_module and (now - last_stale_cleanup_time > 3600):
                 logger.info("Running stale node cleanup...")
                 # Define stale threshold (e.g., nodes not heard from in 2 hours)
                 stationary_module.cleanup_stale_nodes(stale_threshold_seconds=7200)
                 last_stale_cleanup_time = now

            # --- Wait for next check cycle ---
            stop_event.wait(current_check_interval)

        except Exception as e:
            logger.error(f"Error in periodic check loop: {e}", exc_info=True)
            # Avoid tight loop on error
            stop_event.wait(15) # Wait longer after an error

    logger.info("Periodic check loop finished.")


def shutdown_handler(signum, frame):
    """Graceful shutdown handler for SIGINT, SIGTERM."""
    # frame argument is unused but required by signal handler signature
    _ = frame # Suppress unused variable warning
    logger.warning(f"Received signal {signal.Signals(signum).name}. Initiating shutdown...")
    stop_event.set() # Signal all threads to stop

def main():
    """Main application entry point."""
    global meshtastic_interface, notification_module, gps_module, geofence_module, stationary_module, background_threads

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler) # Kill/Systemd stop

    logger.info("--- Akita Geofence Notifier Starting ---")
    logger.info(f"Version: {__version__}")
    logger.info(f"Loading configuration from {CONFIG_FILENAME}...")
    # Config is loaded globally when config.py is imported

    # Initialize core modules
    try:
        geofence_module = GeofenceModule()
        stationary_module = StationaryModule()
        # GPS module initialization depends on config
        if config.gps_serial_port:
             gps_module = GPSModule()
        else:
             logger.info("Serial GPS disabled (gps_serial_port not set in config).")
             gps_module = None # Explicitly set to None

    except Exception as e:
         logger.critical(f"Failed to initialize core modules: {e}", exc_info=True)
         return # Cannot continue

    # Connect to Meshtastic
    meshtastic_interface = connect_meshtastic()
    if not meshtastic_interface:
        logger.critical("Failed to connect to Meshtastic. Exiting.")
        # Perform minimal cleanup if modules were initialized
        if gps_module: gps_module.stop_reading()
        return

    # Initialize notification module now that we have an interface
    notification_module = NotificationModule(meshtastic_interface)

    # Start background threads
    background_threads = [] # Reset list

    if gps_module: # Only start GPS thread if module was initialized
         gps_thread = threading.Thread(target=gps_update_loop, name="GPSUpdateThread", daemon=True)
         background_threads.append(gps_thread)
         gps_thread.start()
    else:
         logger.warning("GPS update thread not started (Serial GPS disabled).")

    # Start the periodic check thread (always runs, relies on mesh data)
    # Ensure modules required by the loop are initialized before starting
    if geofence_module and stationary_module and notification_module:
        check_thread = threading.Thread(target=periodic_check_loop, name="PeriodicCheckThread", daemon=True)
        background_threads.append(check_thread)
        check_thread.start()
    else:
        logger.error("Periodic check thread not started due to missing core modules.")


    # Start the Flask Web Application in a separate thread
    # Ensure modules required by the web app are initialized
    if geofence_module and stationary_module:
        web_thread = threading.Thread(
            target=start_web_app,
            args=(
                notification_queue,
                geofence_module,
                stationary_module,
                config_reload_event, # Pass the event
                config_lock          # Pass the lock
                ),
            name="WebThread",
            daemon=True
            )
        background_threads.append(web_thread)
        web_thread.start()
    else:
         logger.error("Web server thread not started due to missing core modules.")

    logger.info("Application initialization complete. Monitoring...")

    # Keep main thread alive until stop_event is set by signal handler
    try:
        while not stop_event.is_set():
            # Keep main thread alive, threads are daemons
            # Check thread health periodically?
            # for t in background_threads:
            #     if not t.is_alive():
            #         logger.error(f"Thread {t.name} has died unexpectedly!")
            #         # Attempt restart? Or signal shutdown?
            #         # stop_event.set() # Signal shutdown if critical thread dies
            time.sleep(1) # Check stop event every second

    except Exception as e:
         logger.error(f"Unhandled exception in main loop: {e}", exc_info=True)
    finally:
        logger.info("Shutdown initiated. Waiting for threads to finish...")
        # Ensure stop_event is set if exception occurred before signal
        stop_event.set()

        # Wait for background threads to complete
        active_threads = [t for t in background_threads if t.is_alive()]
        while active_threads:
             logger.debug(f"Waiting for threads: {[t.name for t in active_threads]}")
             for thread in active_threads:
                  thread.join(timeout=1.0) # Wait 1 second per thread join attempt
             active_threads = [t for t in background_threads if t.is_alive()]
             if not stop_event.is_set(): # Break if shutdown was cancelled somehow? Unlikely.
                  break

        # Final check on thread status
        for thread in background_threads:
             if thread.is_alive():
                  logger.warning(f"Thread {thread.name} did not exit gracefully after shutdown signal.")

        # Close Meshtastic interface
        if meshtastic_interface:
            logger.info("Closing Meshtastic interface...")
            try:
                meshtastic_interface.close()
            except Exception as e:
                 logger.error(f"Error closing Meshtastic interface: {e}")

        logger.info("--- Akita Geofence Notifier Exited ---")
        logging.shutdown() # Flush and close logging handlers


if __name__ == "__main__":
    # This allows running the script directly, e.g., python -m akita_geofence_notifier.main
    # It assumes config.yaml is in the current working directory.
    main()
