#!/bin/bash
# Run the podman container
# -d: run in background
# --rm: remove container when stopped
# --name: name of the container
# --device: pass the USB serial port to the container
# -p 8067:8080: map host port 8067 to container port 8080

echo "Starting serial-relay-container..."
podman run -d --rm \
  --name serial-relay-container \
  --device /dev/ttyACM0 \
  -p 8067:8080 \
  serial-relay

echo "Container is running!"
echo "You can view logs with: podman logs -f serial-relay-container"
echo "To stop: podman stop serial-relay-container"
