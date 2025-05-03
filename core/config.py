# --- File: akita_geofence_notifier/akita_geofence_notifier/core/config.py ---
import yaml
import os
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any
# Import NodeInfo model to ensure it's defined before potential use (though not directly used here)
from .models import NodeInfo

logger = logging.getLogger(__name__)

# Default config filename, can be overridden if needed
CONFIG_FILENAME = "config.yaml"

@dataclass
class GeofenceConfig:
    """Represents the configuration for a single geofence."""
    name: str
    latitude: float
    longitude: float
    radius_km: float

@dataclass
class AppConfig:
    """Represents the overall application configuration."""
    # --- Meshtastic Settings ---
    private_channel_psk: str = "changeme" # Default secure value, replace or use "" for primary
    # --- GPS Settings ---
    gps_serial_port: str = "/dev/ttyUSB0" # Adjust based on your system
    gps_baud_rate: int = 9600
    # --- Timing Intervals (seconds) ---
    gps_update_interval: int = 60
    check_interval: int = 10
    stationary_time_threshold: int = 300 # 5 minutes
    # --- Stationary Thresholds ---
    stationary_distance_threshold: float = 0.05 # 50 meters (in km)
    # --- Geodetic Calculation ---
    geodetic_radius_km: float = 6371.0
    # --- Web Interface ---
    web_host: str = "0.0.0.0" # Listen on all interfaces
    web_port: int = 5000
    # --- LED Feedback ---
    enable_led_feedback: bool = False # Disabled by default as it needs specific implementation
    led_max_frequency_hz: float = 2.0
    led_min_distance_km: float = 0.1
    # --- Geofences List ---
    geofences: List[GeofenceConfig] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: str = CONFIG_FILENAME) -> 'AppConfig':
        """Loads configuration from a YAML file."""
        if not os.path.exists(config_path):
            logger.warning(f"Configuration file '{config_path}' not found. Using default values.")
            # Optionally create a default config file here based on cls() defaults
            return cls() # Return default config

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)

            if not config_data: # Handle empty config file
                 logger.warning(f"Configuration file '{config_path}' is empty. Using default values.")
                 return cls()

            # Basic validation/parsing for geofences
            geofence_list = []
            if 'geofences' in config_data and isinstance(config_data['geofences'], list):
                for i, gf_data in enumerate(config_data['geofences']):
                    # Check if gf_data is a dictionary before proceeding
                    if not isinstance(gf_data, dict):
                        logger.warning(f"Skipping geofence entry {i+1}: Expected a dictionary, got {type(gf_data)}.")
                        continue
                    try:
                        # Ensure required keys are present
                        if not all(k in gf_data for k in ['name', 'latitude', 'longitude', 'radius_km']):
                             logger.warning(f"Skipping geofence entry {i+1} due to missing keys.")
                             continue
                        # Validate types during instantiation
                        geofence_list.append(GeofenceConfig(**gf_data))
                    except (TypeError, ValueError) as e:
                        logger.warning(f"Skipping geofence entry {i+1} due to invalid data: {e}")

            config_data['geofences'] = geofence_list

            # Use dictionary unpacking, letting dataclass handle defaults for missing keys
            # Filter config_data to only include keys defined in AppConfig to avoid errors
            valid_keys = cls.__annotations__.keys()
            filtered_config_data = {k: v for k, v in config_data.items() if k in valid_keys}

            return cls(**filtered_config_data)

        except yaml.YAMLError as e:
            logger.error(f"Error parsing configuration file '{config_path}': {e}")
            raise # Re-raise after logging
        except Exception as e:
            logger.error(f"Error loading configuration from '{config_path}': {e}")
            raise # Re-raise other errors


# --- Global config instance (load once on import) ---
# This assumes config.yaml is in the directory where the script is run (CWD).
try:
    config = AppConfig.load(CONFIG_FILENAME)
except Exception:
    # Fallback to default config if loading fails catastrophically
    logger.critical("Failed to load configuration. Using default values. Please check/create config.yaml.", exc_info=True)
    config = AppConfig()
