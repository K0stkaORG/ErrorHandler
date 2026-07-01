#!/bin/bash
# Build the podman image
echo "Building serial-relay image..."
podman build -t serial-relay .
echo "Done!"
