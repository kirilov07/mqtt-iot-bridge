// MQTT sensor publisher — ESP8266 / ESP32
// Publishes BME280 readings to topics:  sensors/<device-id>/temperature
//                                        sensors/<device-id>/humidity
//                                        sensors/<device-id>/pressure
// Or as a single JSON message to:       sensors/<device-id>/data

#include <WiFi.h>              // ESP32 — use <ESP8266WiFi.h> for ESP8266
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <ArduinoJson.h>

// ── Config ────────────────────────────────────────────────────────────────────
const char* WIFI_SSID    = "YOUR_SSID";
const char* WIFI_PASS    = "YOUR_PASSWORD";
const char* MQTT_BROKER  = "192.168.1.100";   // IP of your bridge host
const int   MQTT_PORT    = 1883;
const char* DEVICE_ID    = "esp32-01";
const int   PUBLISH_MS   = 5000;              // publish interval

// ── Globals ───────────────────────────────────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);
Adafruit_BME280 bme;

char topicData[64];
char topicStatus[64];

void connectWiFi() {
    Serial.print("WiFi ");
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.println(" OK  IP: " + WiFi.localIP().toString());
}

void connectMQTT() {
    while (!mqtt.connected()) {
        Serial.print("MQTT connecting... ");
        if (mqtt.connect(DEVICE_ID)) {
            Serial.println("OK");
            mqtt.publish(topicStatus, "online", true);  // retained status
        } else {
            Serial.print("failed rc="); Serial.println(mqtt.state());
            delay(3000);
        }
    }
}

void setup() {
    Serial.begin(115200);
    snprintf(topicData,   sizeof(topicData),   "sensors/%s/data",   DEVICE_ID);
    snprintf(topicStatus, sizeof(topicStatus),  "sensors/%s/status", DEVICE_ID);

    connectWiFi();
    mqtt.setServer(MQTT_BROKER, MQTT_PORT);

    if (!bme.begin(0x76)) {
        Serial.println("BME280 not found — check wiring");
        while (1);
    }
}

void loop() {
    if (!mqtt.connected()) connectMQTT();
    mqtt.loop();

    float temp = bme.readTemperature();
    float hum  = bme.readHumidity();
    float pres = bme.readPressure() / 100.0F;

    // Build JSON payload
    StaticJsonDocument<128> doc;
    doc["temp"]  = round(temp * 10) / 10.0;
    doc["hum"]   = round(hum  * 10) / 10.0;
    doc["press"] = round(pres * 10) / 10.0;
    doc["ts"]    = millis() / 1000;

    char payload[128];
    serializeJson(doc, payload, sizeof(payload));

    mqtt.publish(topicData, payload);
    Serial.println(payload);

    delay(PUBLISH_MS);
}
