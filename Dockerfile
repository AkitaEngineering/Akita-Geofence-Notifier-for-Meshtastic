# --- File: Dockerfile ---
# Use an official Python runtime as a parent image
# Choose a version compatible with your requirements (e.g., 3.10, 3.11)
# Slim versions are smaller
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1 # Prevents python from writing .pyc files
ENV PYTHONUNBUFFERED 1      # Prevents python from buffering stdout/stderr

# Set the working directory in the container
WORKDIR /app

# Install system dependencies that might be needed by Python packages
# (e.g., gcc, linux-headers for building wheels, serial port access tools)
# Add 'udev' rules if needed for serial device permissions later, but start simple.
# 'less' and 'vim' are useful for debugging inside the container if needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Add any other system dependencies here if required by your python libs
    # For serial port access, ensure the user running docker has permissions on the host.
    # Sometimes 'dialout' group utils are needed, but often handled by docker device mount.
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements/packaging definition files first to leverage Docker cache
COPY pyproject.toml setup.cfg setup.py MANIFEST.in ./
# Copy the license and readme for reference within the image if desired
COPY LICENSE README.md ./
# Copy requirements.txt for reference if needed (though install uses pyproject.toml)
COPY requirements.txt ./

# Copy the application source code into the container
# Ensure this matches your package directory name
COPY akita_geofence_notifier ./akita_geofence_notifier

# Install Python dependencies using the packaging files
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir .

# NOTE: config.yaml is NOT copied here. It should be mounted as a volume
# using docker-compose or `docker run -v` to allow user configuration.
# The application should handle the case where config.yaml is missing at startup.

# Expose the port the Flask web server runs on (defined in config.yaml, default 5000)
# This informs Docker, but mapping is done in docker run or docker-compose
EXPOSE 5000

# Define the command to run your application
# This uses the entry point defined in pyproject.toml ([project.scripts])
# Running as the 'akita-notifier' command ensures it uses the installed package.
CMD ["akita-notifier"]

# --- Optional: Add non-root user ---
# Running as non-root is best practice for security, but might complicate
# serial device access depending on how permissions are handled.
# If needed:
# RUN useradd --create-home appuser && \
#     # Add user to dialout group if necessary for serial access (may depend on host setup)
#     # addgroup appuser dialout
# USER appuser
# WORKDIR /home/appuser/app # Adjust WORKDIR if using non-root user
# CMD ["akita-notifier"]
