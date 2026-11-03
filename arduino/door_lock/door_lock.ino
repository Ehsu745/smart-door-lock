const byte RELAY_PIN = 7;

const byte RELAY_ON  = LOW;
const byte RELAY_OFF = HIGH;

const unsigned long UNLOCK_DURATION = 8000UL;

String command = "";
bool isUnlocked = false;
unsigned long unlockStartTime = 0;

void lockDoor() {
  digitalWrite(RELAY_PIN, RELAY_OFF);
  isUnlocked = false;
  Serial.println("Locked");
}

void unlockDoor() {
  digitalWrite(RELAY_PIN, RELAY_ON);
  isUnlocked = true;
  unlockStartTime = millis();
  Serial.println("Unlocked: auto-lock in 8 seconds");
}

void setup() {
  digitalWrite(RELAY_PIN, RELAY_OFF);
  pinMode(RELAY_PIN, OUTPUT);

  Serial.begin(9600);
  Serial.println("Ready: enter unlock or lock");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      command.trim();
      command.toLowerCase();

      if (command == "unlock") {
        unlockDoor();
      } else if (command == "lock") {
        lockDoor();
      } else if (command.length() > 0) {
        Serial.println("Unknown command: use unlock or lock");
      }

      command = "";
    } else {
      command += c;
    }
  }

  // 開鎖滿 8 秒後，自動上鎖
  if (isUnlocked &&
      millis() - unlockStartTime >= UNLOCK_DURATION) {
    lockDoor();
  }
}
