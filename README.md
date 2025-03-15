# Akita Geofence Notifier for Meshtastic

This Akita Engineering project implements a geofencing and distance-based notification system for Meshtastic devices, leveraging GPS data. It allows users to define geofences, track node distances, and receive notifications based on various conditions, including entering/exiting geofences, distance changes, and node stationary status.

## Features

* **GPS Integration:** Uses a serial GPS module to obtain accurate location data.
* **Geofencing:** Allows users to define circular geofences around specific locations.
* **Distance Tracking:** Continuously calculates and tracks distances between nodes.
* **Notifications:** Triggers notifications based on geofence entry/exit, distance changes, and node stationary status.
* **Private Channel Notifications:** Sends notifications over a private Meshtastic channel for privacy.
* **LED Feedback:** Modifies the device's LED behavior to indicate proximity and distance changes.
* **Stationary Node Detection:** Notifies when a node has remained stationary for a defined time.

## Requirements

* Meshtastic compatible device (e.g., Heltec Wireless Tracker V1.1).
* Serial GPS module.
* Python 3.
* Meshtastic Python API (`meshtastic`).
* Serial communication library (`pyserial`).
* NMEA sentence parser (`pynmea2`).
* PubSub library (`pubsub`).

## Installation

1.  **Install Python Libraries:**

    ```bash
    pip install meshtastic pyserial pynmea2 pubsub
    ```

2.  **Hardware Setup:**

    * Connect your GPS module to your Meshtastic device's serial port.
    * Ensure your Meshtastic device is configured and connected to the mesh network.

3.  **Clone the Repository (or copy the code):**

    ```bash
    git clone [repository url]
    cd akita_geofence_notifier
    ```

4.  **Configuration:**

    * Edit `main.py` to configure the following:
        * `PRIVATE_CHANNEL_PSK`: Set your private channel pre-shared key.
        * `GPS_SERIAL_PORT`: Adjust the serial port to match your GPS module (e.g., `/dev/ttyUSB0` or `COM3`).
        * `GPS_BAUD_RATE`: Set the baud rate of your GPS module.
        * Geofence setup in the `main()` function: Modify the geofence data to your desired locations and radii.

## Usage

1.  **Run the Script:**

    ```bash
    python main.py
    ```

2.  **Monitoring:**

    * The script will continuously monitor node locations and trigger notifications based on the configured conditions.
    * Notifications will be sent via the Meshtastic mesh, and private notifications will be sent to the configured private channel.
    * The device's LED will provide visual feedback on node proximity.

## Configuration Options

* `NODE_UPDATE_INTERVAL`: GPS location update interval (seconds).
* `DISTANCE_CHECK_INTERVAL`: Distance check interval (seconds).
* `STATIONARY_CHECK_INTERVAL`: Stationary check interval (seconds).
* `STATIONARY_TIME_THRESHOLD`: Time threshold for stationary detection (seconds).
* `PRIVATE_CHANNEL_PSK`: Private channel pre-shared key.
* `GPS_SERIAL_PORT`: Serial port for the GPS module.
* `GPS_BAUD_RATE`: Baud rate for the GPS module.
* Geofence data: Configured in the `main()` function.

## Future Enhancements

* Support for polygon geofences.
* Integration with a mobile app for configuration.
* Improved power optimization.
* More advanced notification options (e.g., audible alerts).
* More robust error handling.

## Contributing

Contributions are welcome! Please submit pull requests or open issues to report bugs or suggest improvements.
