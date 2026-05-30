#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

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

constexpr size_t LORA_MAX_PACKET_SIZE = 255;
constexpr size_t SERIAL_TX_PACKET_SIZE = 4;

volatile bool packetReceived = false;
volatile int receivedPacketLength = 0;
}

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RST);

void initializeDisplay();
void updateDisplay(String text);

void initializeLoRa();
void handleIncomingLoRaPackets();
void handleIncomingSerialPackets();
void transmitLoRaPacket(const uint8_t *packetBuffer, size_t packetLength);
void onReceive(int packetSize);

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);
  delay(1000);

  initializeDisplay();
  initializeLoRa();
}

void loop() {
  handleIncomingSerialPackets();

  if (packetReceived) {
    handleIncomingLoRaPackets();
    packetReceived = false; // Reset RX flag
    LoRa.receive(); 
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
  LoRa.onReceive(onReceive);
  LoRa.receive();

  updateDisplay("LoRa OK");
}

// DIO0 -> High
void onReceive(int packetSize) {
  if (packetSize <= 0) return;
  
  receivedPacketLength = packetSize;
  packetReceived = true;
}

void handleIncomingLoRaPackets() {
  uint8_t packetBuffer[LORA_MAX_PACKET_SIZE];
  size_t packetLength = 0;

  while (LoRa.available() && packetLength < sizeof(packetBuffer)) {
    packetBuffer[packetLength++] = static_cast<uint8_t>(LoRa.read());
  }

  Serial.write(packetBuffer, packetLength); // write to serial
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
