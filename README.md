# ErrorHandler RX — ESP32 LoRa Ground Station Firmware

This repository contains the firmware for the **ErrorHandler RX** ground station, built on a TTGO LoRa32 V1 board. It serves as the primary receiver, logging mechanism, and emergency recovery bridge for the **Segfault Rocket** telemetry.

---

## 🛠️ Hardware Pinout

The firmware configures the ESP32 pins as follows:

*   **OLED Display (I2C):**
    *   `OLED_SDA`: GPIO 21
    *   `OLED_SCL`: GPIO 22
    *   `OLED_RST`: None (-1)
*   **LoRa Transceiver (SPI):**
    *   `LORA_SCK`: GPIO 5
    *   `LORA_MISO`: GPIO 19
    *   `LORA_MOSI`: GPIO 27
    *   `LORA_CS`: GPIO 18
    *   `LORA_RST`: GPIO 14
    *   `LORA_IRQ` (DIO0): GPIO 26

---

## ⚙️ Core Architecture & Mechanisms

The firmware is designed around cooperative scheduling and multi-core safety:

### 1. Thread-Safe LoRa Reception
*   **Physical Layer:** Operates on `439.700 MHz` with a `250 kHz` bandwidth, Spreading Factor `8`, Coding Rate `4/8`, Syncword `0x67`, and a configurable TX Power of `20 dBm`.
*   **Safe Interrupt Pattern:** To prevent crashes on the ESP32 (caused by executing SPI actions inside an Interrupt Service Routine context where the scheduler is suspended), the hardware interrupt attached to `LORA_IRQ` only sets a `volatile bool` flag (`packetReceived = true`).
*   **Main Thread Parsing:** The actual SPI read operation (`LoRa.parsePacket()`) is performed on the main thread inside `loop()`, ensuring thread safety and preventing mutex collision reboots.

### 2. Zero-Latency Flash Logging & Wear Leveling (LittleFS)
*   **Decoupled Writing:** Writing directly to flash can block execution for 10–50 ms. To prevent LoRa packet drops, packets are pushed to a 100-element FreeRTOS queue (`packetQueue`) in RAM.
*   **Buffered background Writes:** A background task (`flashWriterTask`) running on Core 0 pulls packets from the queue and aggregates them in an 8 KB RAM buffer.
*   **Lifespan Preservation:** The buffer is written to the flash filesystem only once every 5 seconds (or immediately if the 8 KB limit is hit). This reduces write frequency by **98%**, saving the flash memory from premature wear.
*   **Log Rotation:** Flight records are written to `/flight.bin`. When the file size exceeds 500 KB, it is rotated to `/flight.bak` (overwriting any previous backup) to maintain a continuous, circular ~1 MB log.

### 3. Emergency Recovery AP & Web Server
*   **SoftAP Configuration:** The ground station hosts an emergency Wi-Fi network. The SSID, WPA2 security password, and local IP address subnet are fully configurable in the setup routine of the firmware.
*   **Web Interface:** Serves three built-in HTTP endpoints:
    *   `/` (Root): Stream-renders the contents of `flight.bak` and `flight.bin` as a formatted hex dump, outputting exactly 33 bytes per line.
    *   `/download`: Initiates a browser download of the complete logs in raw hex format as `flightdata.txt`.
    *   `/flush`: Atomically clears both log files from flash memory for a fresh start.

### 4. OLED Live Statistics
*   **Signal Quality:** Renders the RSSI and SNR of the last packet received.
*   **Latency Monitoring:** Displays the telemetry packet ID and tracks the exact seconds elapsed since the last packet was received.
*   **Rocket Battery Decoding**: Decodes the 1-byte battery telemetry field, converting it to Volts and mapping it linearly between `3.3V` (0%) and `4.2V` (100%) to represent a standard 1S LiPo battery.

---

## 📊 Telemetry Packet Specification (33-Bytes)

The `RocketTelemetry` packet is exactly **33 bytes** long and is transmitted via LoRa. The data is packed strictly (`#pragma pack(push, 1)`) using **Little-Endian** byte order.

| Offset | Length | Field Name | Type | Decoding Formula / Description |
| :--- | :--- | :--- | :--- | :--- |
| `0` | 2 bytes | `syncWord` | `uint16_t` | Constant value `0x5AA5` (hex: `A5 5A` in Little-Endian) for alignment |
| `2` | 2 bytes | `timestampMs` | `uint16_t` | Milliseconds since boot (rolls over every ~65 seconds) |
| `4` | 1 byte | `packetId` | `uint8_t` | Sequential counter (rolls over at 255) |
| `5` | 1 byte | `stateFlags` | `uint8_t` | Enum representing the active avionics flight state (see below) |
| `6` | 2 bytes | `accelX` | `int16_t` | `value / 16384.0` (Acceleration in **g**) |
| `8` | 2 bytes | `accelY` | `int16_t` | `value / 16384.0` (Acceleration in **g**) |
| `10` | 2 bytes | `accelZ` | `int16_t` | `value / 16384.0` (Acceleration in **g**) |
| `12` | 2 bytes | `gyroX` | `int16_t` | `value / 16.4` (Angular rate in **degrees/sec**) |
| `14` | 2 bytes | `gyroY` | `int16_t` | `value / 16.4` (Angular rate in **degrees/sec**) |
| `16` | 2 bytes | `gyroZ` | `int16_t` | `value / 16.4` (Angular rate in **degrees/sec**) |
| `18` | 2 bytes | `kfAltitudeAgl` | `int16_t` | Kalman Filtered Altitude Above Ground Level in **meters** |
| `20` | 2 bytes | `rawPressure` | `uint16_t` | `value * 2.0` (Raw Barometer pressure in **Pascals**) |
| `22` | 2 bytes | `triboVoltage` | `uint16_t` | `value / 1000.0` (Triboelectric probe voltage in **Volts**) |
| `24` | 1 byte | `batteryVoltage` | `uint8_t` | `value * 20` (Battery voltage in **milliVolts**) |
| `25` | 2 bytes | `gpsLatOffset` | `int16_t` | `value / 100000.0` (Offset to add to Base Latitude) |
| `27` | 2 bytes | `gpsLonOffset` | `int16_t` | `value / 100000.0` (Offset to add to Base Longitude) |
| `29` | 2 bytes | `kfVerticalVelocity`| `int16_t` | Kalman Filtered Vertical Velocity in **m/s** |
| `31` | 2 bytes | `ky024Analog` | `uint16_t` | Raw 12-bit ADC reading of the Hall effect breakaway sensor |

---

## 🚀 Avionics FSM States

The rocket's flight computer operates under a Finite State Machine (FSM) transmitted via the `stateFlags` field (Offset 5). The ground station displays or logs these state-dependent behaviors accordingly:

1.  **`BeforeLaunch` (0):** Slow telemetry heartbeat. Calibration baseline calculation active.
2.  **`Armed` (1):** Slow telemetry heartbeat. Parachute deployment circuits armed.
3.  **`Flight` (2):** Fast telemetry transmission active. Ground baseline locked. Flight logging active.
4.  **`ApogeeReached` (3):** Deployment signal dispatched to parachute servo.
5.  **`ChuteDeployed` (4):** Fast telemetry continues. Avionics opens its own local Wi-Fi log retrieval node for post-landing recovery.
