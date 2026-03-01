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

#define CONFIG_FILE       "/config.json"

int  retryCounter      = 0;
const int connectionAttempts = 12;

char  cfgCpmFactor[10] = "0.0057";
float cpmConstant      = 0.0057;
char  cfgDeadTime[6]        = "200";
volatile unsigned long deadTimeMicros = 200;
const int   geigerPin   = 12;
volatile unsigned long impulseCounter;
int  lastPublishMinute = -1;
bool measuring         = false;
unsigned long lastCpm  = 0;
bool hasReading        = false;
int  publishAtSecond   = 0;
bool publishPending    = false;
unsigned long lastLcdUpdate = 0;
char pendingMqttBuf[192];

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
char cfgTimezone[40] = "Europe/Vienna";
const char* lcdDateTimeFmt  = "d.m.y    H:i:s";


// ── Config helpers ────────────────────────────────────────────────────────

int validPort(const char* s) {
  int p = atoi(s);
  return (p >= 1 && p <= 65535) ? p : 1883;
}

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
  strlcpy(cfgTimezone,   doc["timezone"]   | cfgTimezone,   sizeof(cfgTimezone));
  strlcpy(cfgCpmFactor,  doc["cpmFactor"]  | cfgCpmFactor,  sizeof(cfgCpmFactor));
  float parsed = atof(cfgCpmFactor);
  if (parsed > 0) cpmConstant = parsed;
  strlcpy(cfgDeadTime,   doc["deadTime"]   | cfgDeadTime,   sizeof(cfgDeadTime));
  unsigned long dt = strtoul(cfgDeadTime, NULL, 10);
  if (dt > 0 && dt <= 10000) deadTimeMicros = dt;
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
  doc["timezone"]   = cfgTimezone;
  doc["cpmFactor"]  = cfgCpmFactor;
  doc["deadTime"]   = cfgDeadTime;

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
  static unsigned long lastPulse = 0;
  unsigned long now = micros();
  if (now - lastPulse >= deadTimeMicros) {
    impulseCounter++;
    lastPulse = now;
  }
}

void lcdPrintLine(int row, const char* text) {
  lcd.setCursor(0, row);
  char buf[21];
  snprintf(buf, sizeof(buf), "%-20s", text);
  lcd.print(buf);
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
      lcdPrintLine(0, "Connection Error");
      char errLine[21];
      snprintf(errLine, sizeof(errLine), "Error Code: %d", client.state());
      lcdPrintLine(1, errLine);
      char srvLine[21];
      snprintf(srvLine, sizeof(srvLine), "%.20s", cfgMqttServer);
      lcdPrintLine(2, srvLine);

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
        USE_SERIAL.println("MQTT failed — opening config portal");
        retryCounter = 0;

        lcd.clear();
        lcd.setCursor(1, 0);
        lcd.print("MQTT connect failed");
        lcd.setCursor(4, 1);
        lcd.print("AccessPoint:");
        lcd.setCursor(0, 2);
        lcd.print("Name " + deviceHostname);
        lcd.setCursor(0, 3);
        lcd.print("Pass " + wifiApPass);

        WiFiManagerParameter wm_server("mqtt_server", "MQTT Server",   cfgMqttServer, 64);
        WiFiManagerParameter wm_port  ("mqtt_port",   "MQTT Port",     cfgMqttPort,    6);
        WiFiManagerParameter wm_user  ("mqtt_user",   "MQTT User",     cfgMqttUser,   32);
        WiFiManagerParameter wm_pw    ("mqtt_userpw", "MQTT Password", cfgMqttUserPw, 64, " type='password'");
        WiFiManagerParameter wm_pepper("mqtt_pepper", "MQTT Pepper",   cfgMqttPepper, 64, " type='password'");
        WiFiManagerParameter wm_tz    ("timezone",    "Timezone",      cfgTimezone,   40);
        WiFiManagerParameter wm_cpm  ("cpm_factor",  "CPM Factor (uSv/h)", cfgCpmFactor, 10);
        WiFiManagerParameter wm_dt   ("dead_time",   "Dead time (us)",     cfgDeadTime,   6);

        WiFiManager wifiManager;
        wifiManager.setSaveConfigCallback(saveConfigCallback);
        wifiManager.addParameter(&wm_server);
        wifiManager.addParameter(&wm_port);
        wifiManager.addParameter(&wm_user);
        wifiManager.addParameter(&wm_pw);
        wifiManager.addParameter(&wm_pepper);
        wifiManager.addParameter(&wm_tz);
        wifiManager.addParameter(&wm_cpm);
        wifiManager.addParameter(&wm_dt);
        wifiManager.setClass("invert");
        wifiManager.setHostname(deviceHostname.c_str());
        wifiManager.startConfigPortal(deviceHostname.c_str(), wifiApPass.c_str());

        strlcpy(cfgMqttServer, wm_server.getValue(), sizeof(cfgMqttServer));
        strlcpy(cfgMqttPort,   wm_port.getValue(),   sizeof(cfgMqttPort));
        strlcpy(cfgMqttUser,   wm_user.getValue(),   sizeof(cfgMqttUser));
        strlcpy(cfgMqttUserPw, wm_pw.getValue(),     sizeof(cfgMqttUserPw));
        strlcpy(cfgMqttPepper, wm_pepper.getValue(), sizeof(cfgMqttPepper));
        strlcpy(cfgTimezone,   wm_tz.getValue(),      sizeof(cfgTimezone));
        strlcpy(cfgCpmFactor,  wm_cpm.getValue(),     sizeof(cfgCpmFactor));
        {
          float p = atof(cfgCpmFactor);
          if (p > 0) cpmConstant = p;
        }
        strlcpy(cfgDeadTime, wm_dt.getValue(), sizeof(cfgDeadTime));
        {
          unsigned long dt = strtoul(cfgDeadTime, NULL, 10);
          if (dt > 0 && dt <= 10000) deadTimeMicros = dt;
        }

        if (shouldSaveConfig) {
          saveConfig();
          shouldSaveConfig = false;
        }

        deriveMqttCredentials();
        buildMqttStrings();
        client.setServer(cfgMqttServer, validPort(cfgMqttPort));
        continue;
      }

      char attLine[21];
      snprintf(attLine, sizeof(attLine), "Attempt %d of %d", retryCounter, connectionAttempts);
      lcdPrintLine(3, attLine);
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

  if (publishPending) {
    client.publish(mqttTopic.c_str(), pendingMqttBuf);
    publishPending = false;
    USE_SERIAL.println("MQTT published (after reconnect)");
  }
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
  lcdPrintLine(0, " NGNT-GeigerCounter");
  lcdPrintLine(1, "   Initializing...");

  // Load saved MQTT config from flash (fills cfgMqtt* with stored values)
  loadConfig();
  buildMqttStrings();

  // WiFiManager custom parameters — pre-filled with values from flash
  WiFiManagerParameter wm_server("mqtt_server", "MQTT Server",   cfgMqttServer, 64);
  WiFiManagerParameter wm_port  ("mqtt_port",   "MQTT Port",     cfgMqttPort,    6);
  WiFiManagerParameter wm_user  ("mqtt_user",   "MQTT User",     cfgMqttUser,   32);
  WiFiManagerParameter wm_pw    ("mqtt_userpw", "MQTT Password", cfgMqttUserPw, 64, " type='password'");
  WiFiManagerParameter wm_pepper("mqtt_pepper", "MQTT Pepper",   cfgMqttPepper, 64, " type='password'");
  WiFiManagerParameter wm_tz    ("timezone",    "Timezone",      cfgTimezone,   40);
  WiFiManagerParameter wm_cpm  ("cpm_factor",  "CPM Factor (uSv/h)", cfgCpmFactor, 10);
  WiFiManagerParameter wm_dt   ("dead_time",   "Dead time (us)",     cfgDeadTime,   6);

  WiFiManager wifiManager;
  wifiManager.setSaveConfigCallback(saveConfigCallback);
  wifiManager.addParameter(&wm_server);
  wifiManager.addParameter(&wm_port);
  wifiManager.addParameter(&wm_user);
  wifiManager.addParameter(&wm_pw);
  wifiManager.addParameter(&wm_pepper);
  wifiManager.addParameter(&wm_tz);
  wifiManager.addParameter(&wm_cpm);
  wifiManager.addParameter(&wm_dt);
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
  strlcpy(cfgTimezone,   wm_tz.getValue(),      sizeof(cfgTimezone));
  strlcpy(cfgCpmFactor,  wm_cpm.getValue(),     sizeof(cfgCpmFactor));
  {
    float p = atof(cfgCpmFactor);
    if (p > 0) cpmConstant = p;
  }
  strlcpy(cfgDeadTime, wm_dt.getValue(), sizeof(cfgDeadTime));
  {
    unsigned long dt = strtoul(cfgDeadTime, NULL, 10);
    if (dt > 0 && dt <= 10000) deadTimeMicros = dt;
  }

  // Save to flash only if the user submitted the portal
  if (shouldSaveConfig) {
    saveConfig();
    shouldSaveConfig = false;
  }

  // Derive credentials from MAC + pepper (no-op if pepper is empty or
  // the user has customised their MQTT user/password away from defaults)
  deriveMqttCredentials();
  buildMqttStrings();

  client.setServer(cfgMqttServer, validPort(cfgMqttPort));
  client.setKeepAlive(1200);

  lcd.clear();
  lcdPrintLine(0, " NGNT-GeigerCounter");
  lcdPrintLine(1, "  Syncing time...");

  waitForSync();
  Geiger.setLocation(cfgTimezone);

  noInterrupts();
  impulseCounter = 0;
  interrupts();
  lastPublishMinute = Geiger.minute();
  measuring = false;

  randomSeed(ESP.random());  // hardware RNG seed

  lcd.clear();
}


// ── Loop ──────────────────────────────────────────────────────────────────

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  int currentMinute = Geiger.minute();
  int currentSecond = Geiger.second();
  int remaining     = 60 - currentSecond;
  if (remaining == 60) remaining = 0;

  // Minute transition
  if (currentMinute != lastPublishMinute) {
    if (!measuring) {
      // First transition: discard partial window, start measuring
      noInterrupts();
      impulseCounter = 0;
      interrupts();
      measuring = true;
      lcd.clear();
      lastLcdUpdate = 0; // force immediate LCD refresh after clear
      USE_SERIAL.println("Clock aligned — measuring started");
    } else {
      // Subsequent transitions: snapshot the completed minute
      noInterrupts();
      unsigned long cpm = impulseCounter;
      impulseCounter    = 0;
      interrupts();

      // Build MQTT message now (captures the :00 timestamp), publish later
      snprintf(pendingMqttBuf, sizeof(pendingMqttBuf),
               "{\"id\":\"%s\",\"ts\":\"%s\",\"cpm\":%lu,\"usvh\":%.4f}",
               cfgMqttUser, UTC.dateTime("Y-m-d H:i:s").c_str(), cpm,
               cpm * cpmConstant);
      publishAtSecond = random(1, 58);  // 1-57 s
      publishPending  = true;

      lastCpm    = cpm;
      hasReading = true;

      USE_SERIAL.printf("CPM: %lu — publishing at :%02d\n", cpm, publishAtSecond);
    }
    lastPublishMinute = currentMinute;
  }

  // Delayed MQTT publish — spreads load when many devices share a broker
  if (publishPending && currentSecond >= publishAtSecond) {
    client.publish(mqttTopic.c_str(), pendingMqttBuf);
    publishPending = false;
    USE_SERIAL.println("MQTT published");
  }

  // LCD update — throttled to 1 Hz
  if (millis() - lastLcdUpdate >= 1000) {
    lastLcdUpdate = millis();

    // Row 0: live date + time
    char row0[21];
    snprintf(row0, sizeof(row0), "%-20s",
             Geiger.dateTime(lcdDateTimeFmt).c_str());
    lcdPrintLine(0, row0);

    // Row 1: countdown (only while waiting for alignment or first reading)
    if (!measuring) {
      char row1[21];
      snprintf(row1, sizeof(row1), " Waiting for :00 %2ds", remaining);
      lcdPrintLine(1, row1);
    } else if (!hasReading) {
      char row1[21];
      snprintf(row1, sizeof(row1), "  Next reading %3ds", remaining);
      lcdPrintLine(1, row1);
    } else {
      lcdPrintLine(1, "");
    }

    // Rows 2-3: last measurement
    if (hasReading) {
      char row2[21];
      snprintf(row2, sizeof(row2), "     CPM: %lu", lastCpm);
      lcdPrintLine(2, row2);

      char row3[21];
      char usvStr[10];
      dtostrf(lastCpm * cpmConstant, 1, 4, usvStr);
      snprintf(row3, sizeof(row3), "  uSv/h: %s", usvStr);
      lcdPrintLine(3, row3);
    }
  }

  events(); // required by ezTime for periodic NTP re-sync
}
