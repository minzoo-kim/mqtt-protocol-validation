# 장애 복구 검증 증적

실행일: 2026-08-31 KST

## 환경

- Windows ARM64
- Python 3.12.13
- paho-mqtt 2.1.0
- Eclipse Mosquitto 2.x container
- Shopify Toxiproxy 2.12.0 container

## 실행 명령

```powershell
docker compose up -d --wait
.\.venv\Scripts\python.exe -m mqtt_validator run scenarios\resilience.yaml --output-dir reports
.\.venv\Scripts\python.exe scripts\stability_check.py --suite scenarios\resilience.yaml --runs 20
$env:MQTT_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest --cov=mqtt_validator --cov-fail-under=80
```

## 단일 suite 결과

- run ID: `20260831T022826-1272384b`
- 결과: `2 passed, 0 failed`

### TC-MQTT-R001 — Persistent session/offline queue

- 첫 연결 `session_present`: `false`
- 재연결 `session_present`: `true`
- 명시적 재구독: `false`
- offline 상태에서 발행한 QoS 1 message 수신: `true`
- payload 일치: `true`

### TC-MQTT-R002 — Toxiproxy connection cut

- 기존 TCP 연결 강제 종료 감지: `true`
- Paho disconnect reason: `Unspecified error`
- 재연결 성공 시도: 1회
- 재연결·재구독 후 QoS 1 message 수신: `true`
- payload 일치: `true`

## 반복성과 회귀 결과

- 장애 복구 suite 20회 반복: 총 40 case, failure 0
- 전체 pytest: 13 passed
- 최초 장애 복구 검증 당시 statement coverage: 83.31%
- coverage gate: 80% 통과

## MQTT probe 실패 경로 보강 후 회귀 검증

같은 날 연결 timeout 정리, 구독 timeout, publish 미확인, disconnect callback,
bytes payload 경로의 단위 테스트를 추가한 뒤 전체 회귀 시험을 다시 실행했습니다.

- pytest: 20 passed
- 기본 YAML suite: 10 passed, 0 failed
- 장애 복구 YAML suite: 2 passed, 0 failed
- 의도적 실패 suite: 0 passed, 1 failed, process exit code 1
- 변경 후 statement coverage: 83.42%
- 검증 종료 후 Mosquitto/Toxiproxy 컨테이너 중단

이 증적은 실제 차량 ECU/HIL 또는 물리 네트워크 시험이 아닙니다. Toxiproxy가 host TCP 경로를 강제로 닫는 로컬 container 장애 주입과 Mosquitto broker session 동작을 검증한 결과입니다.
