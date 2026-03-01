// Not Great Not Terrible - Geiger Counter
// https://github.com/phoen-ix/ngnt-geiger-counter
//
// LCD -> Wemos D1 R2
// GND -> GND
// VCC -> 3.3V
// SDA -> SDA/D2
// SCL -> SCL/D1
//
// RadiationD Cajoe V1.1 -> Wemos D1 R2
// GND -> GND
// 5V  -> 5V
// VIN -> D6(GPIO12)

// Requires esp8266 Arduino core >= 3.0.0 (for LittleFS)
// In Arduino IDE: Tools -> Board -> Boards Manager -> esp8266 by ESP8266 Community

#include <FS.h>                  // must be first
#include <LittleFS.h>            // built-in, esp8266 core >= 3.0.0
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DNSServer.h>
#include <WiFiManager.h>         //https://github.com/tzapu/WiFiManager v2.0.15-rc.1
#include <ezTime.h>              //https://github.com/ropg/ezTime v0.8.3
#include <PubSubClient.h>        //https://pubsubclient.knolleary.net/ v2.8.0
#include <ArduinoJson.h>         //https://arduinojson.org/ v6 or v7
#include <bearssl/bearssl_hmac.h> // bundled with ESP8266 Arduino core
#define USE_SERIAL Serial

Timezone Geiger;
WiFiClient espClient;
PubSubClient client(espClient);
LiquidCrystal_I2C lcd(0x27, 20, 4);

#define ONE_MINUTE        60000
#define COUNTDOWN_SECONDS 60
#define CONFIG_FILE       "/config.json"

int  retryCounter      = 0;
const int connectionAttempts = 12;

const float cpmConstant = 0.0057;
const int   geigerPin   = 12;
volatile unsigned long impulseCounter;
unsigned long previousMillis;
unsigned long countdownMillis;
bool countdownFinished = false;

// ── Device identity (not user-configurable via portal) ───────────────────
const String deviceHostname = "GeigerCounter";
const String wifiApPass     = "wifiApPass";

// ── MQTT config — stored in flash, editable via the WiFiManager portal ───
// These are the compile-time defaults. Once the portal has been used and
// saved, values are loaded from /config.json on every boot instead.
char cfgMqttServer[64] = "your.server.address";
char cfgMqttPort[6]    = "2883";
char cfgMqttUser[32]   = "geiger00";
char cfgMqttUserPw[64] = "geiger00PW";
char cfgMqttPepper[64] = "";

bool shouldSaveConfig = false;

// ── Derived MQTT strings — rebuilt after config is loaded or saved ────────
String mqttTopic;
String mqttWillMessage;
String mqttConnectedMessage;

// ── Localisation ─────────────────────────────────────────────────────────
const String localTimezone  = "Europe/Vienna";
const String dateOrder      = "d.m.y";
const String timestampOrder = "H:i:s";
const bool   showCountdown  = false;


// ── Config helpers ────────────────────────────────────────────────────────

void buildMqttStrings() {
  mqttTopic            = String("/") + cfgMqttUser + "/impulses";
  mqttWillMessage      = String("{\"id\":\"") + cfgMqttUser + "\",\"status\":\"offline\"}";
  mqttConnectedMessage = String("{\"id\":\"") + cfgMqttUser + "\",\"status\":\"connected\"}";
}

void loadConfig() {
  if (!LittleFS.begin()) {
    USE_SERIAL.println("LittleFS mount failed — using compile-time defaults");
    return;
  }
  if (!LittleFS.exists(CONFIG_FILE)) {
    USE_SERIAL.println("No config file found — using compile-time defaults");
    return;
  }

  File file = LittleFS.open(CONFIG_FILE, "r");
  if (!file) {
    USE_SERIAL.println("Failed to open config file");
    return;
  }

#if defined(ARDUINOJSON_VERSION_MAJOR) && ARDUINOJSON_VERSION_MAJOR >= 7
  JsonDocument doc;
#else
  DynamicJsonDocument doc(512);
#endif

  DeserializationError err = deserializeJson(doc, file);
  file.close();

  if (err) {
    USE_SERIAL.println("Failed to parse config.json — using defaults");
    return;
  }

  strlcpy(cfgMqttServer, doc["mqttServer"] | cfgMqttServer, sizeof(cfgMqttServer));
  strlcpy(cfgMqttPort,   doc["mqttPort"]   | cfgMqttPort,   sizeof(cfgMqttPort));
  strlcpy(cfgMqttUser,   doc["mqttUser"]   | cfgMqttUser,   sizeof(cfgMqttUser));
  strlcpy(cfgMqttUserPw, doc["mqttUserPw"] | cfgMqttUserPw, sizeof(cfgMqttUserPw));
  strlcpy(cfgMqttPepper, doc["mqttPepper"] | cfgMqttPepper, sizeof(cfgMqttPepper));
  USE_SERIAL.println("Config loaded from flash");
}

void saveConfig() {
  if (!LittleFS.begin()) {
    USE_SERIAL.println("LittleFS mount failed — config not saved");
    return;
  }

#if defined(ARDUINOJSON_VERSION_MAJOR) && ARDUINOJSON_VERSION_MAJOR >= 7
  JsonDocument doc;
#else
  DynamicJsonDocument doc(512);
#endif

  doc["mqttServer"] = cfgMqttServer;
  doc["mqttPort"]   = cfgMqttPort;
  doc["mqttUser"]   = cfgMqttUser;
  doc["mqttUserPw"] = cfgMqttUserPw;
  doc["mqttPepper"] = cfgMqttPepper;

  File file = LittleFS.open(CONFIG_FILE, "w");
  if (!file) {
    USE_SERIAL.println("Failed to open config file for writing");
    return;
  }
  serializeJson(doc, file);
  file.close();
  USE_SERIAL.println("Config saved to flash");
}

void deriveMqttCredentials() {
  if (strlen(cfgMqttPepper) == 0 ||
      strcmp(cfgMqttUser, "geiger00") != 0 ||
      strcmp(cfgMqttUserPw, "geiger00PW") != 0) {
    return;
  }

  uint8_t mac[6];
  WiFi.macAddress(mac);
  char macHex[13];
  snprintf(macHex, sizeof(macHex), "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  // Username: geiger_ + last 6 hex chars of MAC
  snprintf(cfgMqttUser, sizeof(cfgMqttUser), "geiger_%s", macHex + 6);

  // Password: HMAC-SHA256(pepper, macHex), first 16 hex chars
  br_hmac_key_context kc;
  br_hmac_context ctx;
  br_hmac_key_init(&kc, &br_sha256_vtable,
                   cfgMqttPepper, strlen(cfgMqttPepper));
  br_hmac_init(&ctx, &kc, 0);
  br_hmac_update(&ctx, macHex, 12);
  uint8_t hmacOut[32];
  br_hmac_out(&ctx, hmacOut);

  for (int i = 0; i < 8; i++) {
    snprintf(cfgMqttUserPw + i * 2, 3, "%02x", hmacOut[i]);
  }
  cfgMqttUserPw[16] = '\0';

  USE_SERIAL.println("── Auto-provisioned MQTT credentials ──");
  USE_SERIAL.printf("  MAC:      %s\n", macHex);
  USE_SERIAL.printf("  Username: %s\n", cfgMqttUser);
  USE_SERIAL.printf("  Password: %s\n", cfgMqttUserPw);
  USE_SERIAL.println("───────────────────────────────────────");
}

void saveConfigCallback() {
  shouldSaveConfig = true;
}


// ── MQTT / interrupt handlers ─────────────────────────────────────────────

void IRAM_ATTR impulse() {
  impulseCounter++;
  if (client.connected()) USE_SERIAL.printf("Impulse detected, number: %lu\n", impulseCounter);
}

void callback(char* topic, byte* payload, unsigned int length) {
  String mqttReceivedMsg;
  for (unsigned int i = 0; i < length; i++) {
    mqttReceivedMsg += (char)payload[i];
  }
}

void reconnect() {
  while (!client.connected()) {
    byte    mqttWillQoS     = 0;
    boolean mqttWillRetain  = false;

    if (client.connect(cfgMqttUser, cfgMqttUser, cfgMqttUserPw,
                       mqttTopic.c_str(), mqttWillQoS, mqttWillRetain,
                       mqttWillMessage.c_str())) {
      client.publish(mqttTopic.c_str(), mqttConnectedMessage.c_str());
    } else {
      retryCounter++;

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Connection Error");
      lcd.setCursor(0, 1);
      lcd.print("Error Code: " + String(client.state()));

      switch (client.state()) {
        case -4: USE_SERIAL.println("-4 MQTT_CONNECTION_TIMEOUT");     break;
        case -3: USE_SERIAL.println("-3 MQTT_CONNECTION_LOST");        break;
        case -2: USE_SERIAL.println("-2 MQTT_CONNECT_FAILED");         break;
        case -1: USE_SERIAL.println("-1 MQTT_DISCONNECTED");           break;
        case  0: USE_SERIAL.println(" 0 MQTT_CONNECTED");              break;
        case  1: USE_SERIAL.println(" 1 MQTT_CONNECT_BAD_PROTOCOL");   break;
        case  2: USE_SERIAL.println(" 2 MQTT_CONNECT_BAD_CLIENT_ID");  break;
        case  3: USE_SERIAL.println(" 3 MQTT_CONNECT_UNAVAILABLE");    break;
        case  4: USE_SERIAL.println(" 4 MQTT_CONNECT_BAD_CREDENTIALS"); break;
        case  5: USE_SERIAL.println(" 5 MQTT_CONNECT_UNAUTHORIZED");   break;
        default: USE_SERIAL.println("Unknown error: " + String(client.state())); break;
      }

      if (retryCounter > connectionAttempts) {
        USE_SERIAL.println("Restarting");
        ESP.restart();
      }

      lcd.setCursor(0, 3);
      lcd.print("Attempt " + String(retryCounter) + " of " + String(connectionAttempts));
      USE_SERIAL.printf("Trying again, attempt %d of %d\n", retryCounter, connectionAttempts);

      WiFiManager wifiManager;
      wifiManager.setConnectTimeout(60);
      wifiManager.setConnectRetries(2);
      if (wifiManager.getWiFiIsSaved()) wifiManager.setEnableConfigPortal(false);
      wifiManager.setHostname(deviceHostname.c_str());
      wifiManager.autoConnect(deviceHostname.c_str(), wifiApPass.c_str());
    }
  }

  retryCounter = 0;
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(Geiger.dateTime(dateOrder));
  lcd.setCursor(5, 2);
  char buffer[10];
  sprintf(buffer, "CPM: %lu", impulseCounter);
  lcd.print(buffer);
  lcd.setCursor(3, 3);
  lcd.print("uSv/h:  ");
  lcd.print(impulseCounter * cpmConstant, 4);
}


// ── Setup ─────────────────────────────────────────────────────────────────

void setup() {
  USE_SERIAL.begin(115200);

  impulseCounter = 0;
  pinMode(geigerPin, INPUT);
  attachInterrupt(digitalPinToInterrupt(geigerPin), impulse, FALLING);

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.home();
  countdownMillis = millis() + ONE_MINUTE;

  // Load saved MQTT config from flash (fills cfgMqtt* with stored values)
  loadConfig();
  buildMqttStrings();

  // WiFiManager custom parameters — pre-filled with values from flash
  WiFiManagerParameter wm_server("mqtt_server", "MQTT Server",   cfgMqttServer, 64);
  WiFiManagerParameter wm_port  ("mqtt_port",   "MQTT Port",     cfgMqttPort,    6);
  WiFiManagerParameter wm_user  ("mqtt_user",   "MQTT User",     cfgMqttUser,   32);
  WiFiManagerParameter wm_pw    ("mqtt_userpw", "MQTT Password", cfgMqttUserPw, 64, " type='password'");
  WiFiManagerParameter wm_pepper("mqtt_pepper", "MQTT Pepper",   cfgMqttPepper, 64, " type='password'");

  WiFiManager wifiManager;
  wifiManager.setSaveConfigCallback(saveConfigCallback);
  wifiManager.addParameter(&wm_server);
  wifiManager.addParameter(&wm_port);
  wifiManager.addParameter(&wm_user);
  wifiManager.addParameter(&wm_pw);
  wifiManager.addParameter(&wm_pepper);
  wifiManager.setClass("invert");
  wifiManager.setScanDispPerc(true);
  wifiManager.setShowInfoErase(false);

  if (!wifiManager.getWiFiIsSaved()) {
    lcd.setCursor(1, 0);
    lcd.print("NGNT-GeigerCounter");
    lcd.setCursor(4, 1);
    lcd.print("AccessPoint:");
    lcd.setCursor(0, 2);
    lcd.print("Name " + deviceHostname);
    lcd.setCursor(0, 3);
    lcd.print("Pass " + wifiApPass);
  }

  wifiManager.setHostname(deviceHostname.c_str());
  wifiManager.autoConnect(deviceHostname.c_str(), wifiApPass.c_str());

  // Always read back — getValue() returns current portal values whether the
  // portal was opened or not (returns the pre-filled defaults if not opened)
  strlcpy(cfgMqttServer, wm_server.getValue(), sizeof(cfgMqttServer));
  strlcpy(cfgMqttPort,   wm_port.getValue(),   sizeof(cfgMqttPort));
  strlcpy(cfgMqttUser,   wm_user.getValue(),   sizeof(cfgMqttUser));
  strlcpy(cfgMqttUserPw, wm_pw.getValue(),     sizeof(cfgMqttUserPw));
  strlcpy(cfgMqttPepper, wm_pepper.getValue(), sizeof(cfgMqttPepper));

  // Save to flash only if the user submitted the portal
  if (shouldSaveConfig) {
    saveConfig();
    shouldSaveConfig = false;
  }

  // Derive credentials from MAC + pepper (no-op if pepper is empty or
  // the user has customised their MQTT user/password away from defaults)
  deriveMqttCredentials();
  buildMqttStrings();

  client.setServer(cfgMqttServer, atoi(cfgMqttPort));
  client.setCallback(callback);
  client.setKeepAlive(1200);

  waitForSync();
  Geiger.setLocation(localTimezone);
  lcd.clear();
}


// ── Loop ──────────────────────────────────────────────────────────────────

void loop() {
  unsigned long currentMillis = millis();

  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  if (!countdownFinished) {
    long remainingSeconds = (countdownMillis - currentMillis) / 1000;
    if (remainingSeconds < 0) remainingSeconds = 0;

    lcd.setCursor(0, 0);
    lcd.print(Geiger.dateTime(dateOrder));
    lcd.setCursor(12, 0);
    lcd.print(Geiger.dateTime(timestampOrder));

    if (showCountdown) {
      char countdownStr[4];
      sprintf(countdownStr, "%2lu", remainingSeconds);
      lcd.setCursor(0, 1);
      lcd.print(countdownStr);
    }

    if (remainingSeconds == 0) countdownFinished = true;
  }

  if (currentMillis - previousMillis > ONE_MINUTE) {
    previousMillis = currentMillis;

    char buffer[10];
    sprintf(buffer, "CPM: %lu", impulseCounter);

    String mqttSendMsg = String("{\"id\":\"")  + cfgMqttUser +
                         "\",\"ts\":\""  + UTC.dateTime("Y-m-d H:i:s") +
                         "\",\"cpm\":"   + String(impulseCounter) +
                         ",\"usvh\":"    + String(impulseCounter * cpmConstant, 4) + "}";
    client.publish(mqttTopic.c_str(), mqttSendMsg.c_str());

    lcd.setCursor(0, 0);
    lcd.print(Geiger.dateTime(dateOrder));
    lcd.setCursor(5, 2);
    lcd.print(buffer);
    lcd.setCursor(3, 3);
    lcd.print("uSv/h:  ");
    lcd.print(impulseCounter * cpmConstant, 4);

    countdownFinished = false;
    countdownMillis   = millis() + ONE_MINUTE;
    impulseCounter    = 0;
    USE_SERIAL.println("Impulse counter reset to 0");
  }

  events(); // required by ezTime for periodic NTP re-sync
}
