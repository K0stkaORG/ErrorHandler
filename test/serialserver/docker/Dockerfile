FROM python:3.11-slim

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the relay script
COPY relay.py .

# Expose the internal TCP port the script listens on
EXPOSE 8080

# The container will run the relay script. 
# It connects to /dev/ttyACM0 by default.
CMD ["python", "-u", "relay.py"]
