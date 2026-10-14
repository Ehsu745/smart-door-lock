#include <string.h>

// IN 接 D7；假設低電位觸發，繼電器啟動時門鎖通電解鎖。
const byte RELAY_PIN = 7;
const byte UNLOCK_LEVEL = LOW;
const byte LOCK_LEVEL = HIGH;
const unsigned long UNLOCK_DURATION = 5000;
bool isUnlocked = false;
unsigned long unlockStartTime = 0;
char commandBuffer[32];
byte commandLength = 0;
bool discardCommand = false;

void lockDoor() {
  digitalWrite(RELAY_PIN, LOCK_LEVEL);
  isUnlocked = false;
  Serial.println("LOCKED");
}
void unlockDoor() {
  digitalWrite(RELAY_PIN, UNLOCK_LEVEL);
  isUnlocked = true;
  unlockStartTime = millis();
  Serial.println("UNLOCKED");
}
void setup() {
  digitalWrite(RELAY_PIN, LOCK_LEVEL);
  pinMode(RELAY_PIN, OUTPUT);
  Serial.begin(9600);
  Serial.println("READY");
}
void loop() {
  while (Serial.available() > 0) {
    char received = Serial.read();
    if (received == '\r') continue;
    if (received == '\n') {
      commandBuffer[commandLength] = '\0';
      if (!discardCommand && commandLength > 0) {
        if (strcmp(commandBuffer, "UNLOCK") == 0) unlockDoor();
        else if (strcmp(commandBuffer, "LOCK") == 0) lockDoor();
        else Serial.println("ERROR: UNKNOWN COMMAND");
      }
      commandLength = 0;
      discardCommand = false;
    } else if (!discardCommand) {
      if (commandLength < sizeof(commandBuffer) - 1) {
        commandBuffer[commandLength++] = received;
      } else {
        discardCommand = true;
        Serial.println("ERROR: COMMAND TOO LONG");
      }
    }
  }
  if (isUnlocked && millis() - unlockStartTime >= UNLOCK_DURATION) lockDoor();
}
