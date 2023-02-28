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

// Arduino IDE esp8266 2.4.2 Version

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <WiFiManager.h>         //https://github.com/tzapu/WiFiManager v2.0.15-rc.1
#include <ezTime.h>             //https://github.com/ropg/ezTime v0.8.3

Timezone Geiger;
WiFiClient espClient;
LiquidCrystal_I2C lcd(0x27, 20, 4); // set the LCD address to 0x27 for a 20 chars and 4 line display

#define ONE_MINUTE 60000
#define COUNTDOWN_SECONDS 60

const float cpm_constant = 0.0057;
const int geiger_pin = 12;
unsigned long impulse_counter;
unsigned long previousMillis;
unsigned long countdownMillis;
bool countdownFinished = false;

const String deviceHostname = "GeigerCounter"; //device Hostname, best to avoid special chars and spaces
const String wifiApPass = "wifiApPass"; //this will be displayed and is only needed to connect to the esp (to setup wifi credentials), this is NOT your home network wifi password

const String localTimezone = "Europe/Vienna"; //CHANGE: set your timezone, see https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
const String dateOrder = "d.m.y"; //change order if you don't like day.month.year see https://github.com/ropg/ezTime#getting-date-and-time

const bool displayCountdown = false; //decide if you want to print a countdown on the display

void impulse() {
  impulse_counter++;
}


void setup() {
  
  WiFiManager wifiManager;
  wifiManager.setClass("invert"); // dark theme
  wifiManager.setScanDispPerc(true); // display percentages instead of graphs for RSSI
  wifiManager.setShowInfoErase(false); // do not show erase button on info page

  impulse_counter = 0;
  pinMode(geiger_pin, INPUT);
  attachInterrupt(digitalPinToInterrupt(geiger_pin), impulse, FALLING);

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.home();
  countdownMillis = millis() + ONE_MINUTE;

  if (!wifiManager.getWiFiIsSaved()){
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
  
  waitForSync();
  Geiger.setLocation(localTimezone); 
  
  lcd.clear();
}


void loop() {
  unsigned long currentMillis = millis();

  if (!countdownFinished) {
    long remainingSeconds = (countdownMillis - currentMillis) / 1000;
    if (remainingSeconds < 0) {
      remainingSeconds = 0;
    }
    
    lcd.setCursor(0, 0);
    lcd.print(Geiger.dateTime(dateOrder)); //change order if you don't like day.month.year see https://github.com/ropg/ezTime#getting-date-and-time
    lcd.setCursor(12, 0);
    lcd.print(Geiger.dateTime("H:i:s"));

    if (displayCountdown){
    char countdownStr[4];
    sprintf(countdownStr, "%2lu", remainingSeconds);
    lcd.setCursor(0, 1);
    lcd.print(countdownStr);
    }
    
    if (remainingSeconds == 0) {
      countdownFinished = true;
    }
  }

  if (currentMillis - previousMillis > ONE_MINUTE) {
    previousMillis = currentMillis;

    char buffer[10];
    sprintf(buffer, "CPM: %d", impulse_counter);

    lcd.setCursor(0, 0);
    lcd.print(Geiger.dateTime(dateOrder)); 
    lcd.setCursor(5, 2);
    lcd.print(buffer);
    lcd.setCursor(3, 3);
    lcd.print("uSv/h:  ");
    lcd.print(impulse_counter * cpm_constant, 4);

    countdownFinished = false;
    countdownMillis = millis() + ONE_MINUTE;
    impulse_counter = 0;
  }
  events(); //needed for ezTime regular timesync and timed events
}
