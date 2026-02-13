# --- File: akita_geofence_notifier/akita_geofence_notifier/core/gps.py ---
import serial
import pynmea2
import time
import threading
import logging
from typing import Optional, Tuple
from .config import config # Use the global config object

logger = logging.getLogger(__name__)

class GPSModule:
    """Handles reading data from a serial GPS module."""
    def __init__(self):
        self.serial_port = config.gps_serial_port
        self.baud_rate = config.gps_baud_rate
        self.ser = None
        self._latitude = None
        self._longitude = None
        self._lock = threading.Lock()       # Lock for accessing lat/lon
        self._stop_event = threading.Event() # Signal to stop the reading thread
        self._thread = None                 # Background reading thread
        self._has_fix = False               # Track if we have ever received a valid fix

    def connect(self) -> bool:
        """Attempts to connect to the serial port."""
        if not self.serial_port:
             logger.info("No GPS serial port configured. Skipping connection.")
             return False
        if self.ser and self.ser.is_open:
            logger.debug("GPS serial port already open.")
            return True
        try:
            # Attempt to open the serial port with a timeout for read operations
            self.ser = serial.Serial(self.serial_port, self.baud_rate, timeout=1)
            logger.info(f"Successfully connected to GPS on {self.serial_port} at {self.baud_rate} baud.")
            return True
        except serial.SerialException as e:
            # Log specific error but don't make it critical, app might run without GPS
            logger.warning(f"Error opening serial port {self.serial_port}: {e}")
            self.ser = None
            return False
        except Exception as e:
             logger.error(f"Unexpected error connecting to GPS: {e}", exc_info=True)
             self.ser = None
             return False

    def _read_loop(self):
        """Internal loop run by the background thread to continuously read GPS data."""
        logger.debug("GPS reading thread started.")
        while not self._stop_event.is_set():
            if not self.ser or not self.ser.is_open:
                logger.debug("GPS serial port not open. Attempting reconnect...")
                if not self.connect():
                    # Wait longer before retrying if connection fails repeatedly
                    self._stop_event.wait(10)
                    continue
                else:
                    logger.info("GPS reconnected.")

            try:
                # Read a line from the serial port, potentially blocking for 'timeout' seconds
                line_bytes = self.ser.readline()
                if not line_bytes: # Timeout occurred, loop again
                    # If we previously had a fix, log that we lost it after a timeout
                    # if self._has_fix:
                    #    logger.debug("GPS signal potentially lost (read timeout).")
                    continue

                # Decode the line, ignoring errors
                line = line_bytes.decode('utf-8', errors='ignore').strip()

                # Process only GPGGA sentences for position fix
                if line.startswith("$GPGGA"):
                    try:
                        msg = pynmea2.parse(line)
                        # Check if it's a valid GGA message with a fix (gps_qual > 0)
                        if isinstance(msg, pynmea2.types.talker.GGA) and msg.gps_qual > 0:
                            # Check for non-zero lat/lon which might indicate valid data
                            if msg.latitude != 0.0 or msg.longitude != 0.0:
                                with self._lock:
                                    self._latitude = msg.latitude
                                    self._longitude = msg.longitude
                                    if not self._has_fix:
                                         logger.info(f"GPS Fix Acquired: Lat={msg.latitude:.5f}, Lon={msg.longitude:.5f}")
                                    self._has_fix = True
                                # logger.debug(f"GPS Update: Lat={msg.latitude:.5f}, Lon={msg.longitude:.5f}")
                            else:
                                # Valid GGA but lat/lon are zero, likely no fix yet
                                if self._has_fix:
                                     logger.info("GPS Fix Lost (Lat/Lon are zero).")
                                self._has_fix = False
                                with self._lock: # Clear last known good location
                                     self._latitude = None
                                     self._longitude = None

                        else: # No GPS fix (gps_qual == 0) or not a GGA message
                             if self._has_fix:
                                  logger.info("GPS Fix Lost (gps_qual=0 or not GGA).")
                             self._has_fix = False
                             with self._lock: # Clear last known good location
                                 self._latitude = None
                                 self._longitude = None

                    except pynmea2.ParseError as e:
                        # Log parsing errors but don't stop the loop
                        logger.warning(f"Failed to parse NMEA sentence: {e} - Line: '{line}'")

            except serial.SerialException as e:
                 logger.error(f"GPS Serial error: {e}. Closing port.")
                 if self.ser:
                     try:
                         self.ser.close()
                    except Exception as e:
                        logger.warning(f"Error closing GPS serial port after SerialException: {e}")
                 self.ser = None # Force reconnect attempt in the next iteration
                 self._has_fix = False # Assume fix lost on serial error
                 with self._lock:
                     self._latitude = None
                     self._longitude = None
                 # Wait before trying to reconnect after serial error
                 self._stop_event.wait(5)
            except UnicodeDecodeError as e:
                logger.warning(f"GPS data decode error: {e}")
            except Exception as e:
                # Catch any other unexpected errors in the loop
                logger.error(f"Unexpected GPS read error: {e}", exc_info=True)
                # Avoid rapid looping on unexpected errors
                self._stop_event.wait(2)

        logger.debug("GPS reading thread finished.")


    def start_reading(self):
        """Starts the background GPS reading thread."""
        if not self.serial_port:
            logger.info("Cannot start GPS reading thread: No serial port configured.")
            return
        if self._thread is None or not self._thread.is_alive():
            # Attempt initial connection before starting thread
            if self.connect():
                 self._stop_event.clear()
                 self._thread = threading.Thread(target=self._read_loop, name="GPSReadThread", daemon=True)
                 self._thread.start()
                 logger.info("GPS reading thread initiated.")
            else:
                 logger.error("Cannot start GPS reading thread, initial connection failed.")
        else:
             logger.debug("GPS reading thread already running.")


    def stop_reading(self):
        """Signals the background GPS reading thread to stop."""
        if self._thread and self._thread.is_alive():
            logger.info("Stopping GPS reading thread...")
            self._stop_event.set()
            self._thread.join(timeout=2) # Wait briefly for thread to exit
            if self._thread.is_alive():
                 logger.warning("GPS reading thread did not stop gracefully.")
            self._thread = None
        if self.ser and self.ser.is_open:
             try:
                 self.ser.close()
                 logger.info("GPS serial port closed.")
             except Exception as e:
                  logger.error(f"Error closing GPS serial port: {e}")
        self.ser = None


    def get_location(self) -> Tuple[Optional[float], Optional[float]]:
        """Returns the latest valid GPS location (latitude, longitude). Thread-safe."""
        with self._lock:
            # Return None if fix was lost or never acquired
            if not self._has_fix:
                return None, None
            return self._latitude, self._longitude

