// ==================================================
// AERONEX node firmware (ESP32-S3 N16R8)
// Based on the hardware team's sketch (aeronex_wrong_thermistor_D0.ino). Only change: the OLED header now says
// "AERONEX NODE" and shows the hardware mode set by the gateway (MODE,normal|warning|silent). Everything else,
// including the CSV_DATA serial line that gateway/serial_bridge.py reads, is unchanged. See firmware/README.md.
// ==================================================
#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_BMP085.h>
#include <DHT.h>
#include <FS.h>
#include <LittleFS.h>

// ==================================================
// AERONEX - ESP32-S3 N16R8
// ==================================================

// ---------------- PIN CONFIGURATION ----------------

#define MQ135_PIN 4
#define MQ2_PIN   5
#define MQ9_PIN   6

#define THERMISTOR_FRONT_PIN 7
#define THERMISTOR_REAR_PIN  8

#define DHT11_PIN 9
#define DHT_TYPE DHT11

#define OLED_SDA 11
#define OLED_SCL 12
#define OLED_ADDR 0x3C

#define BMP180_SDA 18
#define BMP180_SCL 17

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

// ---------------- I2C BUSES ----------------

// Bus 0: OLED
// Bus 1: BMP180
TwoWire OLEDWire(0);
TwoWire BMPWire(1);

// ---------------- SENSOR OBJECTS ----------------

Adafruit_SSD1306 display(
  SCREEN_WIDTH,
  SCREEN_HEIGHT,
  &OLEDWire,
  -1
);

Adafruit_BMP085 bmp;
DHT dht(DHT11_PIN, DHT_TYPE);

bool oledReady = false;
bool bmpReady = false;
bool filesystemReady = false;

const char *LOG_FILE = "/sensor_log.csv";

// ---------------- THERMISTOR SETTINGS ----------------

// Assumes a 10k thermistor and 10k fixed resistor.
// Thermistor connected to 3.3V; fixed resistor to GND.
// ADC reads the junction between them.

const float SERIES_RESISTOR = 10000.0;
const float NOMINAL_RESISTANCE = 10000.0;
const float NOMINAL_TEMP_K = 298.15;
const float BETA_COEFFICIENT = 3950.0;

// ---------------- TIMERS ----------------

unsigned long lastReadTime = 0;
unsigned long lastDisplayPageTime = 0;

const unsigned long READ_INTERVAL = 2000;
const unsigned long PAGE_INTERVAL = 2500;

uint8_t displayPage = 0;
String hardwareMode = "normal";

// ---------------- SENSOR VALUES ----------------

int mq135Value = 0;
int mq2Value = 0;
int mq9Value = 0;

float frontTemp = NAN;
float rearTemp = NAN;

float dhtTemp = NAN;
float humidity = NAN;

float bmpTemp = NAN;
float pressureHpa = NAN;
float altitudeM = NAN;

// ==================================================
// THERMISTOR READING
// ==================================================

float readThermistorC(int pin) {
  int adc = analogRead(pin);

  if (adc <= 0 || adc >= 4095) {
    return NAN;
  }

  // Divider assumed:
  // 3.3V -> thermistor -> ADC -> fixed resistor -> GND

  float resistance =
      SERIES_RESISTOR * ((4095.0 / adc) - 1.0);

  if (resistance <= 0) {
    return NAN;
  }

  float steinhart =
      resistance / NOMINAL_RESISTANCE;

  steinhart = log(steinhart);
  steinhart /= BETA_COEFFICIENT;
  steinhart += 1.0 / NOMINAL_TEMP_K;
  steinhart = 1.0 / steinhart;

  return steinhart - 273.15;
}

// ==================================================
// SERIAL OUTPUT HELPERS
// ==================================================

void printTemperature(const char *label, float value) {
  Serial.print(label);

  if (isnan(value)) {
    Serial.println("Invalid / unavailable");
  } else {
    Serial.print(value, 2);
    Serial.println(" C");
  }
}

void writeCsvFloat(File &file, float value, int decimals = 2) {
  if (isnan(value)) {
    return;
  }

  file.print(value, decimals);
}

void initializeCsvLog() {
  if (!LittleFS.begin(true)) {
    Serial.println("ERROR: LittleFS initialization failed.");
    return;
  }

  filesystemReady = true;

  File logFile = LittleFS.open(LOG_FILE, FILE_APPEND);

  if (!logFile) {
    Serial.println("ERROR: Could not open sensor_log.csv.");
    filesystemReady = false;
    return;
  }

  if (logFile.size() == 0) {
    logFile.println(
      "timestamp_ms,mq135_raw_adc,mq2_raw_adc,mq9_raw_adc,"
      "dht11_temperature_c,dht11_humidity_percent,bmp180_temperature_c,"
      "pressure_hpa,altitude_m"
    );
  }

  logFile.close();
  Serial.println("CSV logging enabled: /sensor_log.csv");
}

void logSensorValuesToCsv() {
  if (!filesystemReady) {
    return;
  }

  File logFile = LittleFS.open(LOG_FILE, FILE_APPEND);

  if (!logFile) {
    Serial.println("ERROR: Could not append to sensor_log.csv.");
    return;
  }

  logFile.print(millis());
  logFile.print(',');
  logFile.print(mq135Value);
  logFile.print(',');
  logFile.print(mq2Value);
  logFile.print(',');
  logFile.print(mq9Value);
  logFile.print(',');
  writeCsvFloat(logFile, dhtTemp);
  logFile.print(',');
  writeCsvFloat(logFile, humidity);
  logFile.print(',');
  writeCsvFloat(logFile, bmpTemp);
  logFile.print(',');
  writeCsvFloat(logFile, pressureHpa);
  logFile.print(',');
  writeCsvFloat(logFile, altitudeM);
  logFile.println();
  logFile.close();
}

void printSerialCsvFloat(float value, int decimals = 2) {
  if (!isnan(value)) {
    Serial.print(value, decimals);
  }
}

void printSerialCsvRow() {
  Serial.print("CSV_DATA,");
  Serial.print(millis());
  Serial.print(',');
  Serial.print(mq135Value);
  Serial.print(',');
  Serial.print(mq2Value);
  Serial.print(',');
  Serial.print(mq9Value);
  Serial.print(',');
  printSerialCsvFloat(dhtTemp);
  Serial.print(',');
  printSerialCsvFloat(humidity);
  Serial.print(',');
  printSerialCsvFloat(bmpTemp);
  Serial.print(',');
  printSerialCsvFloat(pressureHpa);
  Serial.print(',');
  printSerialCsvFloat(altitudeM);
  Serial.println();
}

// ==================================================
// READ ALL SENSORS
// ==================================================

void readAllSensors() {
  // Gas sensor raw ADC readings
  mq135Value = analogRead(MQ135_PIN);
  mq2Value = analogRead(MQ2_PIN);
  mq9Value = analogRead(MQ9_PIN);

  // Thermistors
  frontTemp = readThermistorC(THERMISTOR_FRONT_PIN);
  rearTemp = readThermistorC(THERMISTOR_REAR_PIN);

  // DHT11
  dhtTemp = dht.readTemperature();
  humidity = dht.readHumidity();

  // BMP180
  if (bmpReady) {
    bmpTemp = bmp.readTemperature();
    pressureHpa = bmp.readPressure() / 100.0;
    altitudeM = bmp.readAltitude();
  }
}

// ==================================================
// SERIAL MONITOR REPORT
// ==================================================

void printSerialReport() {
  Serial.println();
  Serial.println("========================================");
  Serial.println("       AERONEX FLEET SENSOR REPORT");
  Serial.println("========================================");

  Serial.print("MQ-135 raw ADC: ");
  Serial.println(mq135Value);

  Serial.print("MQ-2   raw ADC: ");
  Serial.println(mq2Value);

  Serial.print("MQ-9   raw ADC: ");
  Serial.println(mq9Value);

  Serial.println("----------------------------------------");

  printTemperature("Front thermistor: ", frontTemp);
  printTemperature("Rear thermistor:  ", rearTemp);
  printTemperature("DHT11 temperature: ", dhtTemp);

  Serial.print("DHT11 humidity: ");

  if (isnan(humidity)) {
    Serial.println("Invalid / unavailable");
  } else {
    Serial.print(humidity, 2);
    Serial.println(" %");
  }

  Serial.println("----------------------------------------");

  printTemperature("BMP180 temperature: ", bmpTemp);

  Serial.print("Pressure: ");

  if (isnan(pressureHpa)) {
    Serial.println("Unavailable");
  } else {
    Serial.print(pressureHpa, 2);
    Serial.println(" hPa");
  }

  Serial.print("Estimated altitude: ");

  if (isnan(altitudeM)) {
    Serial.println("Unavailable");
  } else {
    Serial.print(altitudeM, 2);
    Serial.println(" m");
  }

  Serial.println("----------------------------------------");
}

// ==================================================
// OLED DISPLAY
// ==================================================

void drawHeader(const char *title) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);

  display.print("AERONEX NODE");
  if (hardwareMode == "warning") {
    display.print(" !WARN");        // set by the gateway (MODE,warning) while this zone's AQI is above 300
  } else if (hardwareMode == "silent") {
    display.print(" (mute)");
  }
  display.println();
  display.println("------------------------");
  display.println(title);
  display.println();
}

void drawGasPage() {
  drawHeader("GAS SENSOR ADC");

  display.print("MQ135: ");
  display.println(mq135Value);

  display.print("MQ2:   ");
  display.println(mq2Value);

  display.print("MQ9:   ");
  display.println(mq9Value);

  display.println();
  display.println("Raw ADC: 0-4095");
}

void drawTemperaturePage() {
  drawHeader("TEMPERATURE / HUMIDITY");

  display.print("Front: ");

  if (isnan(frontTemp)) {
    display.println("--");
  } else {
    display.print(frontTemp, 1);
    display.println(" C");
  }

  display.print("Rear:  ");

  if (isnan(rearTemp)) {
    display.println("--");
  } else {
    display.print(rearTemp, 1);
    display.println(" C");
  }

  display.print("DHT11: ");

  if (isnan(dhtTemp)) {
    display.println("--");
  } else {
    display.print(dhtTemp, 1);
    display.println(" C");
  }

  display.print("Humidity: ");

  if (isnan(humidity)) {
    display.println("--");
  } else {
    display.print(humidity, 1);
    display.println(" %");
  }
}

void drawPressurePage() {
  drawHeader("BMP180 ENVIRONMENT");

  display.print("Temperature: ");

  if (isnan(bmpTemp)) {
    display.println("--");
  } else {
    display.print(bmpTemp, 1);
    display.println(" C");
  }

  display.print("Pressure: ");

  if (isnan(pressureHpa)) {
    display.println("--");
  } else {
    display.print(pressureHpa, 1);
    display.println(" hPa");
  }

  display.print("Altitude: ");

  if (isnan(altitudeM)) {
    display.println("--");
  } else {
    display.print(altitudeM, 1);
    display.println(" m");
  }
}

void animateLogo() {
  // Draw a compact animated AERONEX mark before the sensor pages start.
  for (int frame = 0; frame < 18; frame++) {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);

    // Expanding orbit around the A-shaped mark.
    int radius = 5 + frame * 2;
    display.drawCircle(64, 25, radius, SSD1306_WHITE);
    display.drawLine(54, 34, 64, 13, SSD1306_WHITE);
    display.drawLine(64, 13, 74, 34, SSD1306_WHITE);
    display.drawLine(58, 27, 70, 27, SSD1306_WHITE);

    display.setTextSize(1);
    display.setCursor(39, 43);
    display.print("AERONEX");
    display.setCursor(43, 54);
    display.print("STARTING");

    // Animate a short loading bar.
    display.drawRect(20, 3, 88, 5, SSD1306_WHITE);
    display.fillRect(22, 5, (frame * 5) % 84, 1, SSD1306_WHITE);

    display.display();
    delay(60);
  }
}
void updateOLED() {
  if (!oledReady) {
    return;
  }

  switch (displayPage) {
    case 0:
      drawGasPage();
      break;

    case 1:
      drawTemperaturePage();
      break;

    case 2:
      drawPressurePage();
      break;
  }

  display.display();
}

void readHardwareCommands() {
  if (!Serial.available()) {
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();
  if (!command.startsWith("MODE,")) {
    return;
  }

  String requestedMode = command.substring(5);
  if (requestedMode == "normal" || requestedMode == "warning" || requestedMode == "silent") {
    hardwareMode = requestedMode;
    Serial.print("MODE_SET,");
    Serial.println(hardwareMode);
  } else {
    Serial.println("MODE_ERROR,expected normal|warning|silent");
  }
}

// ==================================================
// SETUP
// ==================================================

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("Starting AERONEX...");

  // ADC configuration
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  pinMode(MQ135_PIN, INPUT);
  pinMode(MQ2_PIN, INPUT);
  pinMode(MQ9_PIN, INPUT);

  pinMode(THERMISTOR_FRONT_PIN, INPUT);
  pinMode(THERMISTOR_REAR_PIN, INPUT);

  // Initialize independent I2C buses
  OLEDWire.begin(OLED_SDA, OLED_SCL, 100000);
  BMPWire.begin(BMP180_SDA, BMP180_SCL, 100000);

  // Initialize OLED
  oledReady = display.begin(
    SSD1306_SWITCHCAPVCC,
    OLED_ADDR,
    true,
    false
  );

  if (oledReady) {
    animateLogo();
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 0);

    display.println("AERONEX");
    display.println();
    display.println("ESP32-S3 N16R8");
    display.println("Initializing...");
    display.display();

    Serial.println("OLED initialized.");
  } else {
    Serial.println("ERROR: OLED initialization failed.");
    Serial.println("Check wiring and I2C address.");
  }

  // Initialize DHT11
  dht.begin();

  // Initialize internal flash storage for CSV logging
  initializeCsvLog();

  // Initialize BMP180 on separate I2C bus
  bmpReady = bmp.begin(
    BMP085_ULTRAHIGHRES,
    &BMPWire
  );

  if (bmpReady) {
    Serial.println("BMP180 initialized.");
  } else {
    Serial.println("ERROR: BMP180 not detected.");
    Serial.println("Check SDA=18, SCL=17 and power.");
  }

  Serial.println("DHT11 initialized.");
  Serial.println("Setup complete.");

  delay(2000);

  lastReadTime = millis();
  lastDisplayPageTime = millis();
}

// ==================================================
// MAIN LOOP
// ==================================================

void loop() {
  unsigned long now = millis();

  readHardwareCommands();

  // Read sensors and print report every 2 seconds
  if (now - lastReadTime >= READ_INTERVAL) {
    lastReadTime = now;

    readAllSensors();
    printSerialReport();
    logSensorValuesToCsv();
    printSerialCsvRow();
  }

  // Rotate OLED pages every 2.5 seconds
  if (now - lastDisplayPageTime >= PAGE_INTERVAL) {
    lastDisplayPageTime = now;
    displayPage = (displayPage + 1) % 3;
  }

  updateOLED();
  delay(50);
}
