# akita_geofence_notifier/main.py

import time
import meshtastic
import threading
import math
import serial
import pynmea2
from pubsub import pub

# --- Configuration ---
NODE_UPDATE_INTERVAL = 60  # seconds
DISTANCE_CHECK_INTERVAL = 10  # seconds
STATIONARY_CHECK_INTERVAL = 30  # seconds
STATIONARY_TIME_THRESHOLD = 120  # seconds
PRIVATE_CHANNEL_PSK = "your_private_psk"
GEODETIC_RADIUS = 6371  # km
GPS_SERIAL_PORT = "/dev/ttyS0"  # Adjust as needed (e.g., /dev/ttyUSB0)
GPS_BAUD_RATE = 9600

# --- Modules ---
class GPSModule:
    def __init__(self, serial_port=GPS_SERIAL_PORT, baud_rate=GPS_BAUD_RATE):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        try:
            self.ser = serial.Serial(self.serial_port, self.baud_rate)
        except serial.SerialException as e:
            print(f"Error opening serial port: {e}")
            self.ser = None
        self.latitude = None
        self.longitude = None

    def get_location(self):
        if self.ser is None:
            return None, None
        try:
            while True:
                line = self.ser.readline().decode('utf-8').strip()
                if line.startswith("$GPGGA"):
                    msg = pynmea2.parse(line)
                    if msg.latitude and msg.longitude: #check for valid data
                        self.latitude = msg.latitude
                        self.longitude = msg.longitude
                        return self.latitude, self.longitude
        except Exception as e:
            print(f"GPS error: {e}")
            return None, None

class GeofenceModule:
    def __init__(self):
        self.geofences = {}  # {node_id: [(lat, lon, radius)]}

    def add_geofence(self, node_id, latitude, longitude, radius):
        if node_id not in self.geofences:
            self.geofences[node_id] = []
        self.geofences[node_id].append((latitude, longitude, radius))

    def is_within_geofence(self, node_id, latitude, longitude):
        if node_id not in self.geofences:
            return False
        for lat, lon, radius in self.geofences[node_id]:
            distance = calculate_distance(latitude, longitude, lat, lon)
            if distance <= radius:
                return True
        return False

class DistanceModule:
    def calculate_distance(self, lat1, lon1, lat2, lon2):
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        lat1 = math.radians(lat1)
        lat2 = math.radians(lat2)

        a = math.sin(dlat / 2) * math.sin(dlat / 2) + \
            math.sin(dlon / 2) * math.sin(dlon / 2) * math.cos(lat1) * math.cos(lat2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance = GEODETIC_RADIUS * c
        return distance

class NotificationModule:
    def __init__(self, meshtastic_connection, private_channel_psk):
        self.meshtastic_connection = meshtastic_connection
        self.private_channel_psk = private_channel_psk

    def send_notification(self, message, private=False):
        if private:
            self.meshtastic_connection.sendText(message, channelId=1, psk=self.private_channel_psk)
        else:
            self.meshtastic_connection.sendText(message)

    def led_notification(self, frequency):
        # Using Meshtastic's LED control (assuming available)
        try:
            if frequency > 0 :
                self.meshtastic_connection.setNodeLED(onTime=0.5/frequency, offTime=0.5/frequency)
            else:
                self.meshtastic_connection.setNodeLED(onTime=1, offTime=0)
        except Exception as e:
            print(f"LED control error: {e}")

class StationaryModule:
    def __init__(self):
        self.last_locations = {}  # {node_id: [(timestamp, lat, lon)]}

    def update_location(self, node_id, latitude, longitude):
        if node_id not in self.last_locations:
            self.last_locations[node_id] = []
        self.last_locations[node_id].append((time.time(), latitude, longitude))
        self.last_locations[node_id] = self.last_locations[node_id][-10:]  # keep last 10

    def is_stationary(self, node_id, threshold):
        if node_id not in self.last_locations or len(self.last_locations[node_id]) < 2:
            return False

        first_time, first_lat, first_lon = self.last_locations[node_id][0]
        last_time, last_lat, last_lon = self.last_locations[node_id][-1]

        if last_time - first_time < threshold:
            return False

        distance = calculate_distance(first_lat, first_lon, last_lat, last_lon)
        if distance < 0.05:  # 50 meters
            return True
        return False

# --- Main Logic ---
def main():
    try:
        interface = meshtastic.serial_interface.SerialInterface()
        gps_module = GPSModule()
        geofence_module = GeofenceModule()
        distance_module = DistanceModule()
        notification_module = NotificationModule(interface, PRIVATE_CHANNEL_PSK)
        stationary_module = StationaryModule()

        # Example geofence setup (replace with user configuration):
        geofence_module.add_geofence("!c0ffee", 43.01, -79.01, 1.0)  # 1km radius

        def update_node_location():
            while True:
                latitude, longitude = gps_module.get_location()
                if latitude is not None and longitude is not None:
                    interface.setGPSLocation(latitude, longitude)
                time.sleep(NODE_UPDATE_INTERVAL)

        def check_distances():
            while True:
                nodes = interface.nodes.values()
                my_lat, my_lon = gps_module.get_location()

                for node in nodes:
                    if "position" in node and node["num"] != interface.myInfo.myNodeNum:
                        node_id = node["user"]["longName"]
                        their_lat = node["position"]["latitude"]
                        their_lon = node["position"]["longitude"]
                        distance = distance_module.calculate_distance(my_lat, my_lon, their_lat, their_lon)
                        stationary_module.update_location(node_id, their_lat, their_lon)

                        if geofence_module.is_within_geofence(node_id, their_lat, their_lon):
                            notification_module.send_notification(f"{node_id} within geofence!", private=True)

                        if stationary_module.is_stationary(node_id, STATIONARY_TIME_THRESHOLD):
                            notification_module.send_notification(f"{node_id} stationary for {STATIONARY_TIME_THRESHOLD} seconds", private=True)

                        notification_module.led_notification(1 / distance if distance > 0 else 1)  # led pulse faster when closer.

                time.sleep(DISTANCE_CHECK_INTERVAL)

        node_update_thread = threading.Thread(target=update_node_location)
        distance_check_thread = threading.Thread(target=check_distances)

        node_update_thread.daemon = True
        distance_check_thread.daemon = True

        node_update_thread.start()
        distance_check_thread.start()

        while True:
            time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")

def calculate_distance(lat1, lon1, lat2, lon2):
    distance_module = DistanceModule()
    return distance_module.calculate_distance(lat1, lon1, lat2, lon2)

if __name__ == "__main__":
    main()
