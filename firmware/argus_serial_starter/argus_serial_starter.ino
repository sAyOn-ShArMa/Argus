/*
  Argus Tier 7 safe serial starter for Arduino or ESP32.

  This sketch controls only LED_BUILTIN. It intentionally contains no motor,
  servo, relay, or autonomous-motion code. Add hardware only after checking the
  board's voltage, current, driver, power, and pin requirements.
*/

const unsigned long ARGUS_BAUD = 115200;
const size_t MAX_LINE_LENGTH = 160;
String inputLine;
bool ledOn = false;

void sendOk(const String &requestId, const String &jsonPayload) {
  Serial.print("ARGUS/1 ");
  Serial.print(requestId);
  Serial.print(" OK ");
  Serial.println(jsonPayload);
}

void sendError(const String &requestId, const String &message) {
  Serial.print("ARGUS/1 ");
  Serial.print(requestId);
  Serial.print(" ERROR ");
  Serial.println(message);
}

void setLed(bool enabled) {
  ledOn = enabled;
  digitalWrite(LED_BUILTIN, enabled ? HIGH : LOW);
}

void processLine(const String &line) {
  if (!line.startsWith("ARGUS/1 ")) {
    return;
  }
  int requestEnd = line.indexOf(' ', 8);
  if (requestEnd < 0) {
    return;
  }
  String requestId = line.substring(8, requestEnd);
  int operationEnd = line.indexOf(' ', requestEnd + 1);
  String operation;
  String arguments;
  if (operationEnd < 0) {
    operation = line.substring(requestEnd + 1);
  } else {
    operation = line.substring(requestEnd + 1, operationEnd);
    arguments = line.substring(operationEnd + 1);
  }

  if (operation == "STATUS") {
    sendOk(
      requestId,
      "{\"state\":\"ready\",\"firmware\":\"argus-serial-starter-1.0\"}"
    );
    return;
  }
  if (operation == "TELEMETRY") {
    String payload = "{\"uptime_ms\":";
    payload += String(millis());
    payload += ",\"led_on\":";
    payload += ledOn ? "true" : "false";
    payload += "}";
    sendOk(requestId, payload);
    return;
  }
  if (operation == "ESTOP") {
    setLed(false);
    sendOk(requestId, "{\"state\":\"emergency_stopped\"}");
    return;
  }
  if (operation == "ACTUATE") {
    int separator = arguments.indexOf(' ');
    if (separator < 0) {
      sendError(requestId, "missing actuator value");
      return;
    }
    String actuator = arguments.substring(0, separator);
    String value = arguments.substring(separator + 1);
    if (actuator != "led" || (value != "0" && value != "1")) {
      sendError(requestId, "only led values 0 or 1 are enabled");
      return;
    }
    setLed(value == "1");
    sendOk(requestId, "{\"state\":\"idle\"}");
    return;
  }
  sendError(requestId, "unknown operation");
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  setLed(false);
  inputLine.reserve(MAX_LINE_LENGTH);
  Serial.begin(ARGUS_BAUD);
}

void loop() {
  while (Serial.available() > 0) {
    char character = static_cast<char>(Serial.read());
    if (character == '\n') {
      inputLine.trim();
      if (inputLine.length() > 0) {
        processLine(inputLine);
      }
      inputLine = "";
    } else if (character != '\r') {
      if (inputLine.length() < MAX_LINE_LENGTH) {
        inputLine += character;
      } else {
        inputLine = "";
      }
    }
  }
}
