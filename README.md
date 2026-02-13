# Akita Geofence Notifier for Meshtastic

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

SPDX-License-Identifier: GPL-3.0-or-later
This Akita Engineering project implements a geofencing, distance-tracking, and notification system for Meshtastic devices, leveraging both external serial GPS modules and GPS data shared over the mesh network.

It allows users to:

* Define circular geofences in a configuration file (`config.yaml`).
* Track node locations received over the Meshtastic network.
* Use a local serial GPS to determine the device's own position and share it.
* Receive notifications via Meshtastic (private channel optional) when nodes enter/exit defined geofences.
* Receive notifications when nodes have remained stationary for a configurable duration.
* Monitor node status, distances, and geofence occupancy via a web interface.
* View and edit configuration settings via the web interface (some changes require restart).
* Optionally provide proximity feedback using the device's LED (experimental, requires hardware-specific setup).

## Features

* **Serial GPS Integration:** Uses a connected serial GPS module (NMEA) for own position.
* **Mesh GPS Tracking:** Listens for position packets from other nodes on the mesh.
* **Configurable Geofencing:** Define multiple circular geofences in `config.yaml`.
* **Distance Tracking:** Calculates distances between the local node and others.
* **Enter/Exit Notifications:** Sends alerts when nodes cross geofence boundaries.
* **Stationary Node Detection:** Notifies when a node hasn't moved significantly for a set time.
* **Private Channel Notifications:** Option to send notifications over a secondary, encrypted channel.
* **Web Interface:** Provides a dashboard to monitor nodes, geofences, status, and recent notifications. Includes a configuration editor.
* **LED Feedback (Optional/Experimental):** Blinks the onboard LED faster as the closest node gets nearer (device/firmware dependent, requires code modification in `meshtastic_utils.py`).
* **Configuration File:** Easy setup via `config.yaml`.
* **Packaging:** Installable via `pip`.
* **Docker Support:** Can be deployed using Docker Compose.

## Requirements

* **Hardware:**
    * Meshtastic compatible device (tested loosely on T-Beam, Heltec). Must be connectable to the machine running this software (USB or TCP).
    * (Optional but Recommended) Serial GPS module (NMEA compatible, e.g., ublox NEO-6M/7M/8M) connected to the machine running this software (if not directly to the Meshtastic device itself - see GPS Setup).
* **Software:**
    * Python 3.8+
    * See `requirements.txt` or `pyproject.toml` for specific Python library dependencies.

## Installation

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/YourUsername/akita_geofence_notifier.git](https://github.com/YourUsername/akita_geofence_notifier.git) # Replace with actual URL
    cd akita_geofence_notifier
    ```

2.  **Set up a Python Virtual Environment (Recommended):**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Linux/macOS
    # or .venv\Scripts\activate # Windows
    ```

3.  **Install the Package:**
    ```bash
    pip install .
    # Or for development (changes reflect immediately):
    # pip install -e .
    ```

4.  **Hardware Setup:**

    * **Meshtastic Device:** Connect your Meshtastic device to the computer via USB or ensure it's accessible via TCP (e.g., `meshtastic --host meshtastic.local --info`).
    * **GPS Setup:**
        * **Option A (Recommended for this software):** Connect the Serial GPS module directly to the computer running this script (e.g., via a USB-to-Serial adapter). Note the serial port name (e.g., `/dev/ttyUSB0`, `COM3`).
        * **Option B (Alternative):** Connect the Serial GPS *directly* to the Meshtastic device's RX/TX pins. In this case, the Meshtastic device itself needs to be configured (via Meshtastic settings) to read from this GPS and broadcast the location. This software will then receive the location over the mesh like any other node. If using Option B, you likely *don't* need to configure `gps_serial_port` in this software's `config.yaml` (set it to `""`).

5.  **Configuration:**
    * **Create `config.yaml`:** Copy the example `config.yaml` content (from Part 2 above) into a new file named `config.yaml` in the project's root directory (where you run the `akita-notifier` command or where `docker-compose.yml` resides).
    * **Edit `config.yaml`:**
        * `private_channel_psk`: Set your **hex-encoded** private channel Pre-Shared Key if you want private notifications on channel 1. Use `""` or `"changeme"` for primary channel (0). Ensure all devices share the same channel settings.
        * `gps_serial_port`: **Crucial (for Option A GPS Setup)!** Set this to the serial port name of the GPS connected *to the computer*. If using Option B GPS Setup, set this to `""`.
        * `gps_baud_rate`: Set the baud rate of your GPS module (commonly 9600).
        * `geofences`: Define your geofence areas. Add or modify entries with `name`, `latitude`, `longitude`, and `radius_km`. Use a site like [LatLong.net](https://www.latlong.net/) to find coordinates.
        * Review and adjust other settings like intervals, thresholds, and web interface host/port as needed.

## Usage (Command Line)

1.  **Activate Virtual Environment (if used):**
    ```bash
    source .venv/bin/activate # Linux/macOS
    # or .venv\Scripts\activate # Windows
    ```

2.  **Ensure `config.yaml` exists** in the current directory.

3.  **Run the Script:**
    ```bash
    akita-notifier
    ```
    * This command is available after installation via `pip install .`.

4.  **Monitoring:**
    * The script will output log messages to the console.
    * It will connect to the Meshtastic device and the GPS module (if configured).
    * Notifications (geofence, stationary) will be sent via Meshtastic.
    * Open a web browser and navigate to `http://<your_device_ip>:5000` (or the host/port configured in `config.yaml`). The default `0.0.0.0` means it should be accessible from other devices on your local network using the IP address of the machine running the script. Use `localhost` or `127.0.0.1` if accessing from the same machine.

5.  **Stopping:** Press `Ctrl+C` in the terminal where the script is running. The script will attempt a graceful shutdown.

## Web Interface

The web UI (`http://<host>:<port>/`) provides:

* **Dashboard (`/`):** Real-time overview of connection status, local node info, detected mesh nodes (with position, distance, etc.), geofence status (which nodes are inside), list of stationary nodes, and recent system notifications. Updates dynamically.
* **Configuration (`/config`):** View and edit settings from `config.yaml`.
    * **Saving:** Changes are saved back to `config.yaml`.
    * **Reloading:** Changes to intervals, thresholds, geofences, and LED settings trigger a "hot reload" in the running application.
    * **Restart Required:** Changes to `gps_serial_port`, `gps_baud_rate`, `private_channel_psk`, `web_host`, or `web_port` require a manual restart of the `akita-notifier` script or Docker container to take effect. The UI will indicate this.
    * **Warning:** Saving overwrites comments and formatting in `config.yaml`. No authentication is built-in.

## Docker Deployment

This application can be deployed using Docker and Docker Compose for easier management.

**Prerequisites:**

* Docker installed ([https://docs.docker.com/get-docker/](https://docs.docker.com/get-docker/))
* Docker Compose installed ([https://docs.docker.com/compose/install/](https://docs.docker.com/compose/install/))
* User running Docker commands needs permission to access the serial device(s) (e.g., GPS module, Meshtastic USB device). On Linux, this usually means adding the user to the `dialout` group:
    ```bash
    sudo usermod -aG dialout $USER
    ```
    You may need to log out and log back in for the group change to take effect.

**Setup:**

1.  **Configuration:** Create a `config.yaml` file in the project root directory (where `docker-compose.yml` is located). Configure it according to your needs. **Crucially, ensure the `gps_serial_port` inside `config.yaml` matches the device path being mounted in `docker-compose.yml` (e.g., `/dev/ttyUSB0`).**
2.  **Device Path:** Edit `docker-compose.yml` and update the `devices` section to map the correct serial device path(s) from your host machine (e.g., change `/dev/ttyUSB0` if your GPS is on `/dev/ttyACM0`). If your Meshtastic device is also connected via USB, map its serial port (e.g., `/dev/ttyACM0`) as well.

**Building and Running:**

1.  **Build the Image (Optional):** Docker Compose will build the image automatically if it doesn't exist. However, you can build it manually:
    ```bash
    docker build -t akita-geofence-notifier:latest .
    ```

2.  **Run with Docker Compose:** From the project root directory:
    ```bash
    # Start in detached mode (runs in background)
    docker-compose up -d

    # Start in foreground (shows logs directly, press Ctrl+C to stop)
    # docker-compose up
    ```

**Accessing the Web UI:**

Once the container is running, access the web interface in your browser at `http://<host_ip>:5000` (replace `<host_ip>` with the IP address of the machine running Docker, or `localhost` if accessing from the same machine).

**Viewing Logs:**

```bash
docker-compose logs -f akita-notifier
```
**Stopping:**

```bash
docker-compose down
```
### Docker Deployment Notes

* The `config.yaml` file is mounted directly from your host. Changes made to this file on the host require restarting the container (`docker-compose restart akita-notifier` or `docker-compose down && docker-compose up -d`) to be fully effective, especially for settings requiring restart.
* Ensure the serial device path(s) in `docker-compose.yml` are correct for your host system.
* Running Docker commands might require `sudo` depending on your Docker installation setup.

### Troubleshooting

* **Cannot connect to Meshtastic:**
    * Ensure device is powered and connected (USB/TCP). Check `meshtastic --info` or `meshtastic --nodes`.
    * Verify serial port permissions (Linux: `dialout` group). Check `dmesg` for connection messages.
    * If using Docker, ensure the correct device is mapped in `docker-compose.yml`.
* **Cannot connect to GPS / No GPS data:**
    * Double-check `gps_serial_port` and `gps_baud_rate` in `config.yaml`.
    * Verify GPS module wiring (TX->RX, RX->TX, GND, VCC) if connected directly.
    * Ensure GPS has power and clear sky view.
    * If using Docker, ensure the correct GPS device path is mapped in `docker-compose.yml`.
    * Use a serial terminal (`minicom`, `tio`, `PuTTY`) on the host (or inside container if tools installed) to check for raw NMEA data from the port.
* **Web UI not accessible:**
    * Check `web_host` and `web_port` in `config.yaml`. `0.0.0.0` allows network access; `127.0.0.1` allows only local access.
    * Check firewall rules on the host machine (and inside container if applicable).
    * Check `docker-compose logs akita-notifier` for web server startup errors.
* **Notifications not sending:**
    * Verify `private_channel_psk` and channel setup on all devices.
    * Check Meshtastic connectivity (`meshtastic --nodes`). Are nodes communicating?
    * Check application logs for errors during notification sending.
* **Configuration changes not taking effect:**
    * Remember that changes to GPS port/baud, PSK, or web host/port require a full restart of the script/container.
    * Other changes should trigger a reload (check logs), but allow time for the next `check_interval`.
    * If using Docker, ensure you restart the container after changing the host's `config.yaml`.

### Future Enhancements / TODO

* Support for polygon geofences.
* Add/Remove geofences via web UI.
* More robust error handling and connection management (e.g., automatic reconnect attempts for Meshtastic).
* Improved LED control implementation with device-specific examples.
* MQTT integration for status/commands.
* Authentication for web UI configuration.
* Handling of stale nodes (removing nodes not heard from after a long time). (Basic cleanup added).
* Unit and integration tests.

### License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0-or-later).

See the [LICENSE](LICENSE) file for full license text.

Copyright (C) 2026 Akita Engineering


### Contributing

Contributions are welcome! Please open an issue to discuss changes or submit a pull request.

### Acknowledgements

* [Meshtastic Project](https://meshtastic.org/)
* Developers of the Python libraries used.

