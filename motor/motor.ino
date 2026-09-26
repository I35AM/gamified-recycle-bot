#include <Servo.h>

// ==========================================
// 1. 핀 번호 및 환경 변수 정의
// ==========================================
#define LED_PIN 13       // LED 제어 (가변저항 밝기 조절용)
#define POT_PIN A0       // 10K 가변저항
#define SERVO_PIN 9      // 쓰레기 투하 서보모터
#define DIR_PIN 4        // TB6560 방향
#define STEP_PIN 5       // TB6560 스텝
#define LIMIT_SWITCH_PIN 3 // 🔥 영점 조준용 리미트 스위치 핀

// 초음파 센서 핀 (Trig: 일반, Echo: 외부 인터럽트)
const int TRIG_PINS[5] = {22, 24, 26, 28, 30};
const int ECHO_PINS[5] = {18, 19, 20, 21, 2};

Servo dropServo;
const int STEPS_PER_BIN = 320; // 1/8 마이크로스텝 기준 72도 (1600 / 5)
int current_bin = 0;           // 현재 바라보는 쓰레기통 인덱스 (0~4)

// 논블로킹 초음파 측정을 위한 volatile 변수
volatile unsigned long start_time[5];
volatile unsigned long echo_duration[5] = {0, 0, 0, 0, 0};
volatile bool new_data[5] = {false, false, false, false, false};
unsigned long last_ping_time = 0;

// ==========================================
// 2. 외부 인터럽트 서비스 루틴 (ISR) - 초음파 전용
// ==========================================
void isr_echo0() { if (digitalRead(ECHO_PINS[0])) start_time[0] = micros(); else { echo_duration[0] = micros() - start_time[0]; new_data[0] = true; } }
void isr_echo1() { if (digitalRead(ECHO_PINS[1])) start_time[1] = micros(); else { echo_duration[1] = micros() - start_time[1]; new_data[1] = true; } }
void isr_echo2() { if (digitalRead(ECHO_PINS[2])) start_time[2] = micros(); else { echo_duration[2] = micros() - start_time[2]; new_data[2] = true; } }
void isr_echo3() { if (digitalRead(ECHO_PINS[3])) start_time[3] = micros(); else { echo_duration[3] = micros() - start_time[3]; new_data[3] = true; } }
void isr_echo4() { if (digitalRead(ECHO_PINS[4])) start_time[4] = micros(); else { echo_duration[4] = micros() - start_time[4]; new_data[4] = true; } }


// ==========================================
// 3. 셋업
// ==========================================
void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  
  // 🔥 스위치 핀 설정 (내부 풀업 저항 사용, 인터럽트 제거됨)
  pinMode(LIMIT_SWITCH_PIN, INPUT_PULLUP);

  dropServo.attach(SERVO_PIN);
  dropServo.write(110); // 투하구 닫힘

  for (int i = 0; i < 5; i++) {
    pinMode(TRIG_PINS[i], OUTPUT);
    pinMode(ECHO_PINS[i], INPUT);
  }
  attachInterrupt(digitalPinToInterrupt(ECHO_PINS[0]), isr_echo0, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ECHO_PINS[1]), isr_echo1, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ECHO_PINS[2]), isr_echo2, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ECHO_PINS[3]), isr_echo3, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ECHO_PINS[4]), isr_echo4, CHANGE);
  
  Serial.println("Mega Ready. Waiting for ROS 2...");

  // 🔥 전원이 켜지면 영점(Homing) 잡기 시작!
  Serial.println("🔄 영점 조준(Homing) 시작...");
  do_homing();
}

// ==========================================
// 4. 메인 루프
// ==========================================
void loop() {
  // A. LED 밝기 실시간 조절
  int potValue = analogRead(POT_PIN);          
  int brightness = map(potValue, 0, 1023, 0, 255); 
  analogWrite(LED_PIN, brightness);            

  // B. 0.5초마다 초음파 센서 쏘고 거리 보고
  if (millis() - last_ping_time > 500) {
    ping_all_sensors();
    report_distances(); 
    last_ping_time = millis();
  }

  // C. 라즈베리파이(ROS 2) 명령 수신 및 모터 작동
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    int target_bin = -1;
    // 클래스에 따른 목표 쓰레기통 매핑
    if (command == "plastic_clean") target_bin = 0;
    else if (command == "can_clean") target_bin = 1;
    else if (command == "glass_clean") target_bin = 2;
    else if (command == "paper_clean") target_bin = 3;
    else if (command == "vinyl_clean") target_bin = 4;

    if (target_bin != -1) {
      // 1. 스텝모터로 최단거리 회전
      rotate_to_bin(target_bin);
      // 2. 서보모터로 쓰레기 투하
      dropServo.write(110);  
      delay(2000); // 2초 대기          
      dropServo.write(60);   
      // 3. 작업 완료 보고
      Serial.println("DONE");
    }
  }
}

// ==========================================
// 5. 🔥 영점 조준(Homing) 실행 함수 (안전 모드)
// ==========================================
void do_homing() {
  digitalWrite(DIR_PIN, LOW); // 스위치를 향해 천천히 회전 (반대로 돌면 HIGH로 변경)

  // 스위치가 눌릴 때까지 무한 반복 (버튼 안 눌림 = HIGH, 눌림 = LOW)
  while (digitalRead(LIMIT_SWITCH_PIN) == HIGH) { 
    digitalWrite(STEP_PIN, HIGH);
    delayMicroseconds(2000); // 더 천천히 회전시켜서 테스트하기 쉽게
    digitalWrite(STEP_PIN, LOW);
    delayMicroseconds(2000);
  }

  // 스위치가 눌려서 while 문을 빠져나왔으므로 0번 칸으로 설정
  current_bin = 0;
  Serial.println("✅ 영점 조준 완료! 현재 위치: 0번 칸");
  delay(1000); // 안정화 대기
}

// ==========================================
// 6. 함수: 초음파 측정 및 거리 보고
// ==========================================
void ping_all_sensors() {
  for (int i = 0; i < 5; i++) {
    digitalWrite(TRIG_PINS[i], LOW); delayMicroseconds(2);
    digitalWrite(TRIG_PINS[i], HIGH); delayMicroseconds(10);
    digitalWrite(TRIG_PINS[i], LOW);
  }
}

void report_distances() {
  Serial.print("DIST:");
  for (int i = 0; i < 5; i++) {
    if (new_data[i]) {
      Serial.print(echo_duration[i] * 0.034 / 2);
      new_data[i] = false;
    } else {
      Serial.print("-1"); // 측정 실패 시
    }
    if (i < 4) Serial.print(",");
  }
  Serial.println();
}

// ==========================================
// 7. 함수: 스텝모터 최단거리 회전 로직
// ==========================================
void rotate_to_bin(int target_bin) {
  if (target_bin == current_bin) return;

  int diff = target_bin - current_bin;
  
  // 원형 구조의 최단 거리 계산 (5칸 기준)
  if (diff > 2) diff -= 5;
  if (diff < -2) diff += 5;

  int steps_to_move = 0;
  if (diff > 0) {
    digitalWrite(DIR_PIN, HIGH); // 시계 방향
    steps_to_move = diff * STEPS_PER_BIN;
  } else {
    digitalWrite(DIR_PIN, LOW);  // 반시계 방향
    steps_to_move = -diff * STEPS_PER_BIN;
  }

  // 모터 구동
  for (int i = 0; i < steps_to_move; i++) {
    digitalWrite(STEP_PIN, HIGH);
    delayMicroseconds(800); // 속도 조절 
    digitalWrite(STEP_PIN, LOW);
    delayMicroseconds(800);
  }

  current_bin = target_bin; 
}