# --- File: akita_geofence_notifier/akita_geofence_notifier/web/app.py ---
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, Response
import threading
import time
import yaml # Import yaml for saving
import os
from queue import Queue, Empty
from datetime import datetime
from copy import deepcopy # Needed for comparing config changes

# Import necessary core components & shared state
# Ensure config is imported first if other modules depend on its global state at import time
from ..core.config import config, AppConfig, GeofenceConfig, CONFIG_FILENAME
from ..core.meshtastic_utils import (
    node_db, node_db_lock,
    my_node_info, my_node_info_lock,
    connection_status, connection_status_lock
)
from ..core.geofence import GeofenceModule
from ..core.stationary import StationaryModule
from ..core.distance import calculate_distance_km
from ..core.models import NodeInfo

# Flask app setup
# Use instance_relative_config=True if you plan to have instance-specific config files later
app = Flask(__name__, template_folder='templates', static_folder='static')
# Generate a secure secret key. In production, set this via environment variable or config file.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))

logger = logging.getLogger(__name__)

# --- Global variables to hold references passed from main ---
_notification_queue: Optional[Queue] = None
_geofence_module: Optional[GeofenceModule] = None
_stationary_module: Optional[StationaryModule] = None
_config_reload_event: Optional[threading.Event] = None
_config_lock: Optional[threading.Lock] = None

# Store recent notifications for display
_recent_notifications: List[str] = []
_max_notifications = 50 # Max number of notifications to keep in memory/display
_notifications_lock = threading.Lock()

def _update_notifications_from_queue():
    """ Internal helper to pull notifications from the shared queue """
    global _recent_notifications
    if not _notification_queue:
        # logger.warning("Notification queue not available.")
        return # Not initialized yet

    try:
        while True: # Get all available notifications in the queue currently
            item = _notification_queue.get_nowait()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Format the message nicely
            msg = item.get('message', 'Unknown Event')
            node_id = item.get('node_id')
            log_entry = f"[{timestamp}] {msg}" + (f" [Node: {node_id}]" if node_id else "")

            with _notifications_lock:
                _recent_notifications.insert(0, log_entry)
                # Limit list size
                _recent_notifications = _recent_notifications[:_max_notifications]
            _notification_queue.task_done() # Mark task as done
    except Empty:
        pass # No more notifications for now
    except Exception as e:
        logger.error(f"Error processing notification queue: {e}", exc_info=True)

# --- Flask Routes ---

@app.route('/')
def index():
    """Main dashboard page."""
    _update_notifications_from_queue() # Check queue on page load

    # Gather data for rendering, ensuring thread safety
    with connection_status_lock:
        conn_status = connection_status.copy()

    with my_node_info_lock:
        # Create a copy or dict representation if needed by template
        local_node_data = vars(my_node_info) if my_node_info else None

    # Get node data and calculate distances
    nodes_list = []
    local_lat, local_lon = (None, None)
    my_id = local_node_data['node_id'] if local_node_data else None

    with node_db_lock:
        # Get local node's position from the DB if available
        if my_id and my_id in node_db:
             local_node_from_db = node_db[my_id]
             local_lat = local_node_from_db.latitude
             local_lon = local_node_from_db.longitude

        # Create a snapshot of the node data for rendering
        for node in node_db.values():
            # Convert NodeInfo dataclass to dict for easier template access
            node_data = vars(node).copy()

            # Calculate distance from local node
            node_data['distance'] = "N/A"
            if local_lat is not None and local_lon is not None and node.node_id != my_id:
                dist = calculate_distance_km(local_lat, local_lon, node.latitude, node.longitude)
                if dist != float('inf'):
                     node_data['distance'] = f"{dist:.2f} km"

            # Format timestamps for display
            node_data['last_heard_str'] = datetime.fromtimestamp(node.last_heard).strftime('%H:%M:%S') if node.last_heard else "N/A"
            node_data['position_time_str'] = datetime.fromtimestamp(node.position_time).strftime('%H:%M:%S') if node.position_time else "N/A"
            # Format battery level
            node_data['battery_str'] = f"{node.battery_level}%" if node.battery_level is not None else "N/A"
            # Format SNR
            node_data['snr_str'] = f"{node.snr:.1f}" if node.snr is not None else "N/A"


            nodes_list.append(node_data)

    # Sort nodes (e.g., by name or last heard) - Optional
    nodes_list.sort(key=lambda x: x['name'] or x['node_id'])

    # Get geofence and stationary status
    geofence_summary = _geofence_module.get_nodes_inside_summary() if _geofence_module else {}
    stationary_nodes_list = _stationary_module.get_stationary_nodes() if _stationary_module else []

    with _notifications_lock:
         current_notifications = _recent_notifications[:] # Copy for rendering

    # Pass the global config object for accessing thresholds etc. in template
    template_config = config

    return render_template('index.html',
                           connection=conn_status,
                           my_node=local_node_data,
                           nodes=nodes_list,
                           geofences=template_config.geofences, # Pass defined fences from config
                           geofence_status=geofence_summary,
                           stationary_nodes=stationary_nodes_list,
                           notifications=current_notifications,
                           config=template_config) # Pass config for thresholds display

@app.route('/api/status')
def api_status():
    """API endpoint to fetch current status for dynamic updates."""
    _update_notifications_from_queue() # Ensure queue is checked

    # Similar data gathering as the index route, but return JSON
    with connection_status_lock:
        conn_status = connection_status.copy()
    with my_node_info_lock:
        local_node_info_dict = vars(my_node_info) if my_node_info else None

    nodes_data = []
    local_lat, local_lon = (None, None)
    my_id = local_node_info_dict['node_id'] if local_node_info_dict else None

    with node_db_lock:
        if my_id and my_id in node_db:
             local_node_from_db = node_db[my_id]
             local_lat = local_node_from_db.latitude
             local_lon = local_node_from_db.longitude

        for node in node_db.values():
             node_dict = vars(node).copy() # Convert dataclass to dict
             # Add distance calculation
             node_dict['distance_km'] = None
             if local_lat is not None and local_lon is not None and node.node_id != my_id:
                  dist = calculate_distance_km(local_lat, local_lon, node.latitude, node.longitude)
                  if dist != float('inf'):
                       node_dict['distance_km'] = dist # Keep as number for potential JS use
             nodes_data.append(node_dict)

    # Sort nodes for consistent API output? Optional.
    nodes_data.sort(key=lambda x: x['name'] or x['node_id'])

    geofence_summary = _geofence_module.get_nodes_inside_summary() if _geofence_module else {}
    stationary_nodes_list = _stationary_module.get_stationary_nodes() if _stationary_module else []
    with _notifications_lock:
         current_notifications = _recent_notifications[:]

    return jsonify({
        'connection': conn_status,
        'my_node': local_node_info_dict,
        'nodes': nodes_data,
        'geofence_status': geofence_summary,
        'stationary_nodes': stationary_nodes_list,
        'notifications': current_notifications,
        'server_time': time.time() # Provide server time for reference
    })

@app.route('/config', methods=['GET', 'POST'])
def config_page():
    """Page to view and edit configuration."""
    global config # We need to potentially modify the global config object

    # Ensure lock is available
    if not _config_lock:
         flash("Configuration lock not initialized. Cannot edit config.", "error")
         # Redirect or render an error template
         return redirect(url_for('index'))

    if request.method == 'POST':
        restart_required = False
        reload_triggered = False
        try:
            with _config_lock: # Acquire lock before modifying config
                 # Store previous critical settings to detect changes
                 old_config_copy = deepcopy(config)

                 # --- Create a dictionary from form data ---
                 new_config_data = {}
                 try:
                     # Basic types
                     new_config_data['private_channel_psk'] = request.form.get('private_channel_psk', '').strip()
                     new_config_data['gps_serial_port'] = request.form.get('gps_serial_port', '').strip()
                     new_config_data['gps_baud_rate'] = int(request.form.get('gps_baud_rate', 9600))
                     new_config_data['gps_update_interval'] = int(request.form.get('gps_update_interval', 60))
                     new_config_data['check_interval'] = int(request.form.get('check_interval', 10))
                     new_config_data['stationary_time_threshold'] = int(request.form.get('stationary_time_threshold', 300))
                     new_config_data['stationary_distance_threshold'] = float(request.form.get('stationary_distance_threshold', 0.05))
                     new_config_data['web_host'] = request.form.get('web_host', '0.0.0.0').strip()
                     new_config_data['web_port'] = int(request.form.get('web_port', 5000))
                     new_config_data['enable_led_feedback'] = 'enable_led_feedback' in request.form
                     new_config_data['led_max_frequency_hz'] = float(request.form.get('led_max_frequency_hz', 2.0))
                     new_config_data['led_min_distance_km'] = float(request.form.get('led_min_distance_km', 0.1))
                     # geodetic_radius_km is likely constant, keep existing value
                     new_config_data['geodetic_radius_km'] = config.geodetic_radius_km
                 except ValueError as e:
                      raise ValueError(f"Invalid numeric value submitted: {e}") from e


                 # --- Process Geofences ---
                 new_geofences = []
                 i = 0
                 while True:
                     # Check if fields for this index exist using a hidden input or name pattern
                     fence_name_key = f'geofence_{i}_name'
                     if fence_name_key not in request.form:
                         break # No more fences in the form

                     try:
                          name = request.form.get(fence_name_key, f'Fence {i+1}').strip()
                          lat = float(request.form.get(f'geofence_{i}_latitude', 0.0))
                          lon = float(request.form.get(f'geofence_{i}_longitude', 0.0))
                          radius = float(request.form.get(f'geofence_{i}_radius_km', 0.1))

                          if not name: name = f"Unnamed Fence {i+1}"
                          # Ensure positive radius, prevent zero or negative
                          if radius <= 0:
                               raise ValueError(f"Geofence '{name}' radius must be positive.")

                          new_geofences.append(GeofenceConfig(name=name, latitude=lat, longitude=lon, radius_km=radius))
                     except ValueError as e:
                           # Re-raise with more context
                           raise ValueError(f"Invalid data for Geofence {i+1} ('{name}'): {e}") from e
                     except Exception as e:
                           raise RuntimeError(f"Error processing Geofence {i+1}: {e}") from e
                     i += 1
                 new_config_data['geofences'] = new_geofences

                 # --- Validate (basic range checks) ---
                 if new_config_data['gps_update_interval'] <= 0 or new_config_data['check_interval'] <= 0:
                      raise ValueError("Intervals must be positive.")
                 if not (0 <= new_config_data['web_port'] <= 65535):
                      raise ValueError("Web Port must be between 0 and 65535.")
                 if new_config_data['led_max_frequency_hz'] < 0 or new_config_data['led_min_distance_km'] < 0:
                      raise ValueError("LED frequency and distance cannot be negative.")
                 if new_config_data['stationary_distance_threshold'] < 0:
                      raise ValueError("Stationary distance threshold cannot be negative.")


                 # --- Create the new AppConfig object (will raise errors if types are wrong) ---
                 updated_config_obj = AppConfig(**new_config_data)


                 # --- Check for changes requiring restart ---
                 # Compare critical fields between the new object and the old copy
                 if (updated_config_obj.gps_serial_port != old_config_copy.gps_serial_port or
                     updated_config_obj.gps_baud_rate != old_config_copy.gps_baud_rate or
                     updated_config_obj.private_channel_psk != old_config_copy.private_channel_psk or
                     updated_config_obj.web_host != old_config_copy.web_host or
                     updated_config_obj.web_port != old_config_copy.web_port):
                     restart_required = True

                 # --- Save to YAML file ---
                 try:
                     # Convert AppConfig back to a plain dict for dumping
                     dict_to_save = vars(updated_config_obj).copy()
                     dict_to_save['geofences'] = [vars(gf) for gf in updated_config_obj.geofences]

                     # Save to the config file path (assumed to be in CWD)
                     with open(CONFIG_FILENAME, 'w', encoding='utf-8') as f:
                         yaml.dump(dict_to_save, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                     logger.info(f"Configuration saved successfully to {CONFIG_FILENAME}")
                 except IOError as e:
                     logger.error(f"Error saving configuration file {CONFIG_FILENAME}: {e}")
                     flash(f"Error saving configuration file: {e}", "error")
                     # Re-render with old config as save failed
                     return render_template('config.html', current_config=old_config_copy)
                 except yaml.YAMLError as e:
                     logger.error(f"Error formatting data for YAML saving: {e}")
                     flash(f"Error formatting configuration for saving: {e}", "error")
                     return render_template('config.html', current_config=old_config_copy)


                 # --- Update the global config object IN MEMORY ---
                 # Replace the existing global 'config' object content
                 for key, value in vars(updated_config_obj).items():
                      setattr(config, key, value)
                 logger.info("In-memory configuration updated.")


                 # --- Signal background threads ---
                 if _config_reload_event:
                      # Check if any reloadable parameters actually changed
                      if (updated_config_obj.check_interval != old_config_copy.check_interval or
                          updated_config_obj.stationary_time_threshold != old_config_copy.stationary_time_threshold or
                          updated_config_obj.stationary_distance_threshold != old_config_copy.stationary_distance_threshold or
                          updated_config_obj.enable_led_feedback != old_config_copy.enable_led_feedback or
                          updated_config_obj.led_max_frequency_hz != old_config_copy.led_max_frequency_hz or
                          updated_config_obj.led_min_distance_km != old_config_copy.led_min_distance_km or
                          updated_config_obj.geofences != old_config_copy.geofences): # Compare geofence lists

                          _config_reload_event.set()
                          reload_triggered = True
                          logger.info("Config reload event set for background threads.")
                          # Schedule clearing of the event after a delay to allow threads to process
                          threading.Timer(2.0, lambda: _config_reload_event.clear() if _config_reload_event else None).start()
                          logger.debug("Scheduled config reload event clear in 2s.")
                      else:
                           logger.info("No changes detected requiring background thread reload.")

                 # --- Set Flash Message ---
                 if restart_required:
                     flash("Configuration saved. Restart required for GPS/Meshtastic/Web server changes to take effect.", "warning")
                 elif reload_triggered:
                     flash("Configuration saved. Reload triggered for applicable settings (intervals, thresholds, geofences, LED).", "success")
                 else:
                      flash("Configuration saved. No changes detected requiring reload or restart.", "info")

                 # Redirect to GET request to show updated config and prevent resubmission
                 return redirect(url_for('config_page'))

        except (ValueError, RuntimeError) as e:
             # Handle validation errors from form processing
             logger.error(f"Invalid data submitted in config form: {e}")
             flash(f"Invalid data submitted: {e}", "error")
             # Render the form again, showing the *old* config as we couldn't parse/validate the new one
             # Need to acquire lock again for reading current config state
             with _config_lock:
                  return render_template('config.html', current_config=config)
        except Exception as e:
             logger.error(f"Unexpected error processing config form: {e}", exc_info=True)
             flash(f"An unexpected error occurred while saving: {e}", "error")
             with _config_lock:
                  return render_template('config.html', current_config=config)


    # --- Handle GET request ---
    # Display current configuration (read directly from the global config object)
    with _config_lock: # Ensure reading consistent state
        # Pass a deep copy to prevent accidental modification? Or trust template not to change it.
        # Let's pass the object directly for simplicity.
        return render_template('config.html', current_config=config)


# Function to start the Flask app (called from main.py)
def start_web_app(notification_q: Queue, geofence_mod: GeofenceModule,
                   stationary_mod: StationaryModule, reload_event: threading.Event, cfg_lock: threading.Lock):
    """Initializes and runs the Flask web application."""
    global _notification_queue, _geofence_module, _stationary_module, _config_reload_event, _config_lock
    _notification_queue = notification_q
    _geofence_module = geofence_mod
    _stationary_module = stationary_mod
    _config_reload_event = reload_event
    _config_lock = cfg_lock

    host = config.web_host
    port = config.web_port
    logger.info(f"Attempting to start Flask web server on http://{host}:{port}")

    try:
        # Use Flask's built-in server for development/simplicity.
        # For production, consider using a WSGI server like Waitress or Gunicorn.
        # Example with Waitress:
        # from waitress import serve
        # serve(app, host=host, port=port, threads=4) # Adjust threads as needed
        app.run(host=host, port=port, debug=False, use_reloader=False) # debug=False, reloader=False important when run by main script
        logger.info("Flask web server stopped.")
    except OSError as e:
         # Common error: Port already in use
         logger.error(f"Failed to start Flask web server on {host}:{port}. Error: {e}", exc_info=True)
         logger.error("Please check if another application is using this port or change the 'web_port' in config.yaml.")
    except Exception as e:
        logger.error(f"Flask web server encountered an unexpected error: {e}", exc_info=True)

