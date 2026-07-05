#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <WebServer.h>

#define OLED_SDA 21
#define OLED_SCL 22
#define OLED_RST -1
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

#define LORA_SCK 5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_CS 18
#define LORA_RST 14
#define LORA_IRQ 26

namespace {
constexpr long SERIAL_BAUD_RATE = 115200;
constexpr long LORA_FREQUENCY = 439700000; // 439.700 MHz
constexpr long LORA_BANDWIDTH = 250000; // 250 kHz
constexpr int  LORA_SF = 8;
constexpr int  LORA_CR = 8; // 4/8
constexpr int  LORA_SW = 0x67; // syncword
constexpr int  LORA_TX_POWER = 20; // 20 dBm TX power

constexpr size_t LORA_MAX_PACKET_SIZE = 255;
constexpr size_t SERIAL_TX_PACKET_SIZE = 4;

volatile bool packetReceived = false;
volatile int receivedPacketLength = 0;

uint8_t lastPacketId = 0;
unsigned long lastPacketReceivedMillis = 0;
bool hasTelemetry = false;
unsigned long lastDisplayUpdate = 0;
uint8_t lastBatteryVoltageRaw = 0;

WebServer server(80);
QueueHandle_t packetQueue;

struct RawPacket {
  uint8_t data[LORA_MAX_PACKET_SIZE];
  size_t length;
};

const char* HTML_HEAD = R"=====(
<!DOCTYPE html>
<html>
<head>
<title>Rocket Flight Data</title>
<style>
  body { font-family: monospace; background: #222; color: #eee; padding: 20px; }
  .btn { display: inline-block; padding: 10px 20px; margin: 10px 5px; background: #007bff; color: white; text-decoration: none; border-radius: 5px; }
  .btn-danger { background: #dc3545; }
  .data { background: #111; padding: 10px; overflow-x: auto; white-space: pre; }
</style>
</head>
<body>
<h1>Rocket Flight Data Backup</h1>
<div>
  <a href="/download" class="btn">Download flightdata.txt</a>
  <a href="/flush" class="btn btn-danger" onclick="return confirm('Are you sure?')">Flush Memory</a>
</div>
<h2>Hex Dump</h2>
<div class="data">
)=====";

const char* HTML_TAIL = R"=====(
</div>
</body>
</html>
)=====";

void sendHexDump(File& f) {
  if (!f) return;
  uint8_t buf[33];
  char hexBuf[33 * 3 + 2];
  while (f.available()) {
    int bytesRead = f.read(buf, 33);
    int hexIndex = 0;
    for (int i = 0; i < bytesRead; i++) {
      sprintf(&hexBuf[hexIndex], "%02X ", buf[i]);
      hexIndex += 3;
    }
    hexBuf[hexIndex++] = '\n';
    hexBuf[hexIndex] = '\0';
    server.sendContent(hexBuf);
  }
}

void handleRoot() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "text/html", HTML_HEAD);
  
  File fBak = LittleFS.open("/flight.bak", FILE_READ);
  sendHexDump(fBak);
  if (fBak) fBak.close();

  File fBin = LittleFS.open("/flight.bin", FILE_READ);
  sendHexDump(fBin);
  if (fBin) fBin.close();

  server.sendContent(HTML_TAIL);
  server.sendContent(""); 
}

void handleDownload() {
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.sendHeader("Content-Disposition", "attachment; filename=flightdata.txt");
  server.send(200, "text/plain", "");

  File fBak = LittleFS.open("/flight.bak", FILE_READ);
  sendHexDump(fBak);
  if (fBak) fBak.close();

  File fBin = LittleFS.open("/flight.bin", FILE_READ);
  sendHexDump(fBin);
  if (fBin) fBin.close();

  server.sendContent("");
}

void handleFlush() {
  LittleFS.remove("/flight.bak");
  LittleFS.remove("/flight.bin");
  server.sendHeader("Location", "/");
  server.send(303);
}

void flashWriterTask(void *pvParameters) {
  const size_t BUFFER_LIMIT = 8192; // 8KB buffer to hold ~5.6 seconds of data at 50Hz
  uint8_t *writeBuffer = (uint8_t *)malloc(BUFFER_LIMIT);
  if (!writeBuffer) {
    // If malloc fails (very unlikely), delete task
    vTaskDelete(NULL);
    return;
  }
  size_t bufferOffset = 0;
  unsigned long lastWriteMillis = millis();

  while (true) {
    RawPacket pkt;
    // Wake up periodically to check queue (timeout of 100ms)
    if (xQueueReceive(packetQueue, &pkt, pdMS_TO_TICKS(100)) == pdTRUE) {
      if (bufferOffset + pkt.length <= BUFFER_LIMIT) {
        memcpy(writeBuffer + bufferOffset, pkt.data, pkt.length);
        bufferOffset += pkt.length;
      } else {
        // Buffer is full, force write to flash immediately
        File f = LittleFS.open("/flight.bin", FILE_APPEND);
        if (f) {
          f.write(writeBuffer, bufferOffset);
          size_t size = f.size();
          f.close();
          if (size > 500000) { 
            LittleFS.remove("/flight.bak");
            LittleFS.rename("/flight.bin", "/flight.bak");
          }
        }
        
        // Copy the current packet into the start of the cleared buffer
        memcpy(writeBuffer, pkt.data, pkt.length);
        bufferOffset = pkt.length;
        lastWriteMillis = millis();
      }
    }

    // Write to flash every 5 seconds if there is data
    if (bufferOffset > 0 && (millis() - lastWriteMillis >= 5000)) {
      File f = LittleFS.open("/flight.bin", FILE_APPEND);
      if (f) {
        f.write(writeBuffer, bufferOffset);
        size_t size = f.size();
        f.close();
        if (size > 500000) { 
          LittleFS.remove("/flight.bak");
          LittleFS.rename("/flight.bin", "/flight.bak");
        }
      }
      bufferOffset = 0;
      lastWriteMillis = millis();
    }
  }
}
}


#pragma pack(push, 1)
struct RocketTelemetry {
  uint16_t syncWord;
  uint16_t timestampMs;
  uint8_t packetId;
  uint8_t stateFlags;

  int16_t accelX;
  int16_t accelY;
  int16_t accelZ;
  int16_t gyroX;
  int16_t gyroY;
  int16_t gyroZ;

  int16_t kfAltitudeAgl;
  uint16_t rawPressure;
  uint16_t triboVoltage;
  uint8_t batteryVoltage;

  int16_t gpsLatOffset;
  int16_t gpsLonOffset;
  int16_t kfVerticalVelocity;
  uint16_t ky024Analog;
};
#pragma pack(pop)

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RST);

void initializeDisplay();
void updateDisplay(String text);

void initializeLoRa();
void handleIncomingLoRaPackets();
void handleIncomingSerialPackets();
void transmitLoRaPacket(const uint8_t *packetBuffer, size_t packetLength);
void IRAM_ATTR onReceiveInterrupt();

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);
  delay(1000);

  initializeDisplay();
  initializeLoRa();

  if (!LittleFS.begin(true)) {
    updateDisplay("FS Mount FAIL");
  }

  packetQueue = xQueueCreate(100, sizeof(RawPacket));
  xTaskCreatePinnedToCore(flashWriterTask, "FlashWriter", 4096, NULL, 1, NULL, 0);

  IPAddress IP(172, 27, 67, 1);
  IPAddress NMask(255, 255, 255, 240);
  WiFi.softAPConfig(IP, IP, NMask);
  WiFi.softAP("TestingInProduction", "hlinena67");

  server.on("/", handleRoot);
  server.on("/download", handleDownload);
  server.on("/flush", handleFlush);
  server.begin();
}

void loop() {
  server.handleClient();
  handleIncomingSerialPackets();

  if (packetReceived) {
    packetReceived = false; // Reset RX flag
    if (LoRa.parsePacket() > 0) {
      handleIncomingLoRaPackets();
    }
    LoRa.receive(); 
  }

  if (hasTelemetry && millis() - lastDisplayUpdate >= 250) {
    lastDisplayUpdate = millis();
    unsigned long secondsAgo = (millis() - lastPacketReceivedMillis) / 1000;
    
    display.fillRect(0, 24, SCREEN_WIDTH, 16, SSD1306_BLACK);
    
    display.setCursor(0, 24);
    display.print("ID ");
    display.print(lastPacketId);
    display.print(" ");
    display.print(secondsAgo);
    display.print("s ago");

    float battV = lastBatteryVoltageRaw * 20.0f / 1000.0f;
    int battPct = (battV - 3.3f) / (4.2f - 3.3f) * 100;
    if (battPct > 100) battPct = 100;
    if (battPct < 0) battPct = 0;

    display.setCursor(0, 32);
    display.print("Rocket ");
    display.print(battV, 1);
    display.print("V ");
    display.print(battPct);
    display.print("%");

    display.display();
  }
}

void initializeDisplay() {
  Wire.begin(OLED_SDA, OLED_SCL);
  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.display();
  }
}

void updateDisplay(String text) {
  display.fillRect(0, 16, SCREEN_WIDTH, 8, SSD1306_BLACK);
  display.setCursor(0, 16);
  display.print(text);
  display.display();
}

void initializeLoRa() {
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_IRQ);

  if (!LoRa.begin(LORA_FREQUENCY)) {
    updateDisplay("LoRa FAIL");
    while (true) {
      delay(1000);
    }
  }

  LoRa.setSignalBandwidth(LORA_BANDWIDTH);
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SW); 
  LoRa.setTxPower(LORA_TX_POWER);
  pinMode(LORA_IRQ, INPUT);
  attachInterrupt(digitalPinToInterrupt(LORA_IRQ), onReceiveInterrupt, RISING);
  LoRa.receive();

  updateDisplay("LoRa OK");
}

void IRAM_ATTR onReceiveInterrupt() {
  packetReceived = true;
}

void handleIncomingLoRaPackets() {
  uint8_t packetBuffer[LORA_MAX_PACKET_SIZE];
  size_t packetLength = 0;

  while (LoRa.available() && packetLength < sizeof(packetBuffer)) {
    packetBuffer[packetLength++] = static_cast<uint8_t>(LoRa.read());
  }

  Serial.write(packetBuffer, packetLength); // write to serial

  // Enqueue for backup storage
  RawPacket pkt;
  pkt.length = packetLength;
  memcpy(pkt.data, packetBuffer, packetLength);
  // Do not block, drop if full (Option A)
  xQueueSend(packetQueue, &pkt, 0);
  
  if (packetLength == sizeof(RocketTelemetry)) {
    RocketTelemetry* telemetry = reinterpret_cast<RocketTelemetry*>(packetBuffer);
    lastPacketId = telemetry->packetId;
    lastBatteryVoltageRaw = telemetry->batteryVoltage;
    lastPacketReceivedMillis = millis();
    hasTelemetry = true;
  }

  // TODO? display basic data
  updateDisplay("RX " + String(LoRa.packetRssi()) + " dBm " + String(LoRa.packetSnr()) + " dB"); // display signal
}

void handleIncomingSerialPackets() {
  static uint8_t packetBuffer[SERIAL_TX_PACKET_SIZE];
  static size_t packetLength = 0;

  while (Serial.available()) {
    packetBuffer[packetLength++] = static_cast<uint8_t>(Serial.read());

    if (packetLength == sizeof(packetBuffer)) {
      transmitLoRaPacket(packetBuffer, packetLength);
      packetLength = 0;
    }
  }
}

void transmitLoRaPacket(const uint8_t *packetBuffer, size_t packetLength) {
  LoRa.idle();
  LoRa.beginPacket();
  LoRa.write(packetBuffer, packetLength);
  LoRa.endPacket();
  LoRa.receive();

  updateDisplay("TX " + String(packetLength) + " bytes");
}
