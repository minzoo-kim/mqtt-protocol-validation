# 처음 배우는 MQTT와 이 프로젝트 사용법

이 문서는 MQTT, Docker, 테스트 자동화를 처음 접하는 사람을 위한 설명서입니다. 명령을 외우기 전에 “무엇을 왜 검사하는가”부터 이해하는 것이 목표입니다.

## 1. 이 프로젝트를 한 문장으로 설명하면

> 여러 프로그램이 MQTT로 메시지를 제대로 주고받는지, 문제가 생겼을 때 예상한 방식으로 실패하고 복구하는지를 자동으로 검사하는 도구입니다.

자동차를 예로 들면 다음과 같은 메시지가 오갈 수 있습니다.

- 차량 앱이 `문 잠금 요청`을 전송한다.
- 차량 제어기가 요청을 받는다.
- 제어기가 `문 잠금 완료` 상태를 전송한다.
- 통신이 잠시 끊겼다가 다시 연결된다.

이 프로젝트는 실제 차량을 제어하지 않습니다. 대신 로컬 PC 안에서 메시지를 보내는 프로그램과 받는 프로그램을 만들어 MQTT 통신의 기본 동작을 시험합니다.

## 2. MQTT란 무엇인가

MQTT는 작은 메시지를 여러 프로그램 사이에서 주고받기 위한 통신 규칙입니다. 차량, 센서, IoT 기기처럼 네트워크가 느리거나 불안정할 수 있는 환경에서 많이 사용됩니다.

단체 채팅방에 비유하면 이해하기 쉽습니다.

| MQTT 용어 | 단체 채팅 비유 | 이 프로젝트에서 하는 일 |
|---|---|---|
| Broker | 채팅 서버 | Mosquitto가 메시지를 받아 필요한 사람에게 전달 |
| Publisher | 메시지를 보내는 사람 | Python 프로그램이 테스트 메시지를 발행 |
| Subscriber | 채팅방을 구독한 사람 | Python 프로그램이 특정 topic의 메시지를 수신 |
| Topic | 채팅방 이름 | `validator/.../qos1` 같은 메시지 주소 |
| Payload | 채팅 내용 | JSON 형식의 `message_id`, 상태, 값 |
| Publish | 채팅 보내기 | publisher가 broker에 메시지를 전송 |
| Subscribe | 채팅방 들어가기 | subscriber가 받고 싶은 topic을 등록 |

MQTT에서는 publisher가 subscriber의 주소를 직접 알 필요가 없습니다.

```text
Publisher → Broker(Mosquitto) → Subscriber
```

publisher는 broker에 메시지를 보내고, broker가 해당 topic을 구독한 subscriber에게 전달합니다. 이 구조 덕분에 보내는 프로그램과 받는 프로그램을 서로 독립적으로 만들 수 있습니다.

## 3. 이 프로젝트가 검사하는 MQTT 개념

### 3.1 QoS 0, 1, 2

QoS는 메시지를 얼마나 확실하게 전달할지 정하는 단계입니다.

| QoS | 쉬운 비유 | 특징 |
|---:|---|---|
| 0 | 일반 우편 | 한 번 보내고 끝냄. 빠르지만 손실될 수 있음 |
| 1 | 수령 확인 우편 | 적어도 한 번 전달. 확인 과정에서 중복될 수 있음 |
| 2 | 인계 절차를 여러 번 확인 | 정확히 한 번 전달을 목표로 하지만 가장 느리고 복잡함 |

이 프로젝트의 QoS 0 테스트는 “절대 잃어버리지 않는다”를 증명하지 않습니다. 정상적인 로컬 연결에서 메시지 한 건이 도착하는지를 확인합니다.

QoS 2 테스트도 네트워크 패킷 네 단계를 직접 분석하지는 않습니다. subscriber가 결과 메시지를 한 번 관측하는지를 확인합니다. 패킷 단계까지 증명하려면 나중에 Wireshark/tshark 검증을 추가해야 합니다.

### 3.2 Retained message

일반 메시지는 subscriber가 늦게 접속하면 놓칠 수 있습니다. retained message는 broker가 topic의 마지막 상태를 기억했다가, 나중에 접속한 subscriber에게 전달합니다.

게시판에 붙여둔 최신 공지와 비슷합니다.

```text
1. Publisher가 READY 상태를 retained로 전송
2. Broker가 READY를 기억
3. Subscriber가 나중에 접속
4. Subscriber가 READY를 즉시 수신
```

테스트가 끝나면 다음 실행에 영향을 주지 않도록 저장된 retained 상태를 지웁니다.

### 3.3 중복 메시지

QoS 1이나 애플리케이션 재시도 때문에 같은 업무 메시지가 두 번 도착할 수 있습니다. 이 프로젝트는 동일한 `message_id`를 가진 메시지 두 건을 보내고 다음처럼 집계되는지 확인합니다.

```text
실제 수신: 2건
고유 message_id: 1개
중복: 1건
```

현재 검증은 애플리케이션 `message_id` 기준입니다. MQTT 패킷의 DUP bit를 강제로 조작한 시험은 아닙니다.

### 3.4 Malformed payload

정상 JSON은 다음처럼 중괄호와 값이 완전합니다.

```json
{"message_id":"m-1","state":"READY"}
```

아래처럼 JSON이 중간에서 끊기면 malformed payload입니다.

```text
{"message_id":"broken",
```

이 프로젝트는 잘못된 JSON을 정상 데이터처럼 처리하지 않고 `JSONDecodeError`로 거부했는지 기록합니다.

### 3.5 Timeout

정해진 시간 안에 메시지가 오지 않는 상황입니다. 모든 timeout이 프로그램 오류인 것은 아닙니다.

- `메시지가 오지 않아야 한다`는 TC에서는 timeout이 정상 결과입니다.
- `메시지가 와야 한다`는 TC에서는 timeout이 실패입니다.

즉, 단순히 timeout이라는 단어만 보고 실패로 정하지 않고 테스트의 Expected와 비교합니다.

### 3.6 Disconnect와 reconnect

차량이 터널에 들어가거나 네트워크가 바뀌면 연결이 끊길 수 있습니다. 이 프로젝트는 subscriber가 연결을 끊은 뒤 다시 연결하고, topic을 다시 구독한 다음 메시지를 받을 수 있는지 검사합니다.

기본 suite의 reconnect는 프로그램이 정상적인 disconnect를 요청한 뒤 reconnect하는 시험입니다. 복구 suite에서는 아래 두 가지 더 현실적인 상황을 별도로 검사합니다.

### 3.7 Toxiproxy connection cut

Toxiproxy는 프로그램과 Mosquitto 사이에 놓는 장애 주입용 TCP 중계기입니다.

```text
정상: Subscriber ↔ Toxiproxy ↔ Mosquitto
장애: Subscriber ↔ [연결 강제 종료]  Mosquitto
복구: Subscriber ↔ Toxiproxy ↔ Mosquitto
```

검증기는 이미 연결된 subscriber가 있는 상태에서 proxy를 비활성화합니다. 그러면 실제 TCP 연결이 닫히고 Paho callback에 비정상 disconnect가 전달됩니다. 이후 proxy를 다시 켜고, subscriber가 reconnect·재구독한 뒤 새 메시지를 받는지 자동 판정합니다.

이것은 단순히 `disconnect()` 함수를 호출하는 시험보다 실제 네트워크 단절에 가깝습니다. 다만 packet loss나 latency를 주입하는 시험은 아직 아닙니다.

### 3.8 Persistent session과 offline queue

`clean_session=false`로 연결한 subscriber는 연결이 끊겨도 broker에 구독 session을 남길 수 있습니다.

```text
1. Subscriber가 QoS 1 topic 구독
2. Subscriber가 offline 상태로 전환
3. Publisher가 QoS 1 메시지 발행
4. Mosquitto가 메시지를 offline queue에 보관
5. 같은 client_id로 reconnect
6. 재구독하지 않고 기존 session과 queued message 복원
```

검증기는 두 번째 연결에서 broker의 `session_present=true` 응답, `resubscribed=false`, queued message 수신과 payload 일치를 확인합니다. 시험 후에는 다음 실행을 오염시키지 않도록 broker에 남은 session을 지웁니다.

## 4. 자동 검증은 어떤 순서로 동작하는가

사람이 화면을 보며 “메시지가 온 것 같다”고 판단하지 않습니다. 다음 과정을 코드가 수행합니다.

```text
요구사항 ID
   ↓
YAML 테스트케이스
   ↓
Publisher/Subscriber 실행
   ↓
실제 결과 관측
   ↓
Expected와 Observed 비교
   ↓
Pass/Fail 및 리포트 생성
```

예를 들어 QoS 1 테스트는 다음과 같습니다.

```yaml
- id: TC-MQTT-002
  requirement_id: REQ-MQTT-QOS1
  name: QoS 1 acknowledged delivery
  kind: publish_receive
  topic: validator/{run_id}/qos1
  qos: 1
  subscribe_qos: 1
  payload:
    message_id: qos1-{run_id}
    value: 20
  expected:
    received_count: 1
    delivered_qos: 1
    payload_matches: true
```

중요한 필드는 다음과 같습니다.

- `id`: 테스트케이스 번호
- `requirement_id`: 어떤 요구사항을 확인하는지 나타내는 번호
- `kind`: 실행할 시험 방식
- `topic`: 사용할 MQTT 주소
- `qos`, `subscribe_qos`: 발행과 구독의 QoS
- `payload`: 보낼 내용
- `expected`: 통과로 인정할 결과
- `{run_id}`: 실행할 때마다 바뀌는 고유 값

`{run_id}`를 사용하는 이유는 이전 테스트 메시지나 동시에 실행한 테스트가 현재 결과에 섞이지 않도록 하기 위해서입니다.

## 5. 폴더와 파일은 무엇인가

```text
mqtt-protocol-validation/
├── docker-compose.yml       Mosquitto 실행 방법
├── docker/                  Mosquitto 설정
├── scenarios/
│   ├── mvp.yaml             통과해야 하는 10개 TC
│   ├── resilience.yaml      TCP cut과 persistent session 2개 TC
│   └── failure_demo.yaml    일부러 실패시키는 TC
├── src/mqtt_validator/      실제 Python 검증 프로그램
├── tests/                   pytest 테스트
├── reports/                 실행 후 생성되는 결과 파일
├── scripts/                 20회 반복 검사
├── .github/workflows/       GitHub Actions 자동화 설정
└── docs/                    기술 설명과 검증 증적
```

핵심 실행 흐름은 다음 세 파일에서 확인할 수 있습니다.

1. `scenarios/mvp.yaml`: 무엇을 시험하는가
2. `src/mqtt_validator/runner.py`: 어떤 순서로 시험하는가
3. `src/mqtt_validator/reporters.py`: 결과를 어떻게 파일로 만드는가

## 6. 실행 방법

### 6.1 Docker Desktop 시작

Windows 시작 메뉴에서 `Docker Desktop`을 실행합니다. 작업 표시줄에 Docker 고래 아이콘이 나타나고 engine이 준비될 때까지 기다립니다.

### 6.2 PowerShell에서 프로젝트 폴더로 이동

```powershell
cd <repository-path>
```

### 6.3 Mosquitto broker 실행

```powershell
docker compose up -d --wait
```

옵션의 뜻은 다음과 같습니다.

- `up`: 필요한 container를 만들고 실행
- `-d`: 터미널 뒤에서 실행
- `--wait`: health check가 통과할 때까지 기다림

상태 확인:

```powershell
docker compose ps
```

`healthy`가 표시되면 준비된 것입니다.

### 6.4 MQTT 테스트 10개 실행

가상환경은 이미 만들어져 있으므로 다음 명령을 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m mqtt_validator run scenarios\mvp.yaml --output-dir reports
```

성공하면 다음과 비슷하게 표시됩니다.

```text
Running 10 scenarios against 127.0.0.1:1883
Result: 10 passed, 0 failed, total 10
JSON: ...
JUNIT: ...
JSONL: ...
```

### 6.5 장애 복구 테스트 2개 실행

```powershell
.\.venv\Scripts\python.exe -m mqtt_validator run scenarios\resilience.yaml --output-dir reports
```

성공하면 다음처럼 표시됩니다.

```text
Running 2 scenarios against 127.0.0.1:1883
Result: 2 passed, 0 failed, total 2
```

- `TC-MQTT-R001`: persistent session과 offline QoS 1 queue
- `TC-MQTT-R002`: Toxiproxy connection cut과 reconnect 복구

### 6.6 결과 확인

`reports` 폴더에 세 종류가 생성됩니다.

| 파일 | 쉽게 말하면 | 주 사용처 |
|---|---|---|
| `.json` | 전체 시험 성적표 | 사람이 결과 요약과 Expected/Observed 확인 |
| `.xml` | 자동화 도구용 시험 성적표 | GitHub Actions와 CI 시스템 |
| `.jsonl` | 시간순 상세 일지 | 연결·구독·발행·수신·실패 원인 분석 |

JSON 결과에서 먼저 볼 곳은 `summary`입니다.

```json
{
  "total": 10,
  "passed": 10,
  "failed": 0,
  "success": true
}
```

그다음 각 `results` 안의 다음 항목을 비교합니다.

- `expected`: 원래 기대한 결과
- `observed`: 실제로 관측한 결과
- `mismatches`: 서로 다른 항목
- `error`: 프로그램이나 연결에서 발생한 예외

### 6.7 사용이 끝나면 broker 중단

```powershell
docker compose down
```

이 명령은 실행 중인 Mosquitto·Toxiproxy container와 이 프로젝트용 Docker network를 정리합니다. 프로젝트 코드와 YAML은 지워지지 않습니다. 다음에 다시 `docker compose up -d --wait`를 실행하면 동일하게 재생성됩니다.

## 7. 의도적 실패 재현

자동 판정기가 잘못된 Expected를 실제 결과와 구분하는지 확인하기 위한 fixture입니다.

```powershell
.\.venv\Scripts\python.exe -m mqtt_validator run scenarios\failure_demo.yaml --output-dir reports
```

이 TC는 subscriber가 실제로 받은 QoS 1을 Expected QoS 2와 비교하도록 일부러 잘못 설정했습니다.

```text
delivered_qos: expected 2, observed 1
```

그래서 결과는 `0 passed, 1 failed`이고 프로그램 종료 코드는 1입니다. 이것은 검증기가 정상적으로 결함을 잡았다는 뜻입니다.

실패를 알면서 리포트만 만들고 명령 자체는 성공 처리하고 싶다면 다음 옵션을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m mqtt_validator run scenarios\failure_demo.yaml --output-dir reports --allow-failures
```

## 8. pytest는 무엇인가

MQTT 검증기가 다른 시스템을 시험한다면, pytest는 이 검증기 자체가 제대로 만들어졌는지를 시험합니다.

```text
MQTT validator → MQTT 동작을 검사
pytest         → MQTT validator 코드가 올바른지 검사
```

broker 없이 빠른 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Mosquitto까지 포함한 전체 테스트:

```powershell
$env:MQTT_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest
```

현재 프로젝트에는 다음 검사가 있습니다.

- YAML의 잘못된 QoS와 중복 TC ID 거부
- payload JSON 인코딩
- Expected/Observed 비교
- JSON/JUnit/JSONL 리포트 생성
- 실제 Mosquitto를 이용한 10개 시나리오 통합 시험
- 실제 Toxiproxy와 Mosquitto를 이용한 장애 복구 2개 통합 시험

## 9. GitHub Actions란 무엇인가

GitHub Actions는 GitHub가 제공하는 **원격 자동 실행 로봇**이라고 생각하면 됩니다.

내 PC에서 테스트하면 “내 PC에서는 됩니다”까지만 증명할 수 있습니다. GitHub Actions를 사용하면 GitHub가 매번 깨끗한 가상 컴퓨터를 빌려 다음 작업을 자동으로 실행합니다.

```text
코드를 GitHub에 push
        ↓
GitHub가 새 가상 PC 준비
        ↓
Python과 의존성 설치
        ↓
Docker Compose로 Mosquitto와 Toxiproxy 시작
        ↓
pytest와 MQTT 기본 10개·복구 2개 TC 실행
        ↓
성공/실패 표시 + 리포트 보관
```

중요 용어는 다음과 같습니다.

| 용어 | 의미 | 이 프로젝트의 예 |
|---|---|---|
| Event | 자동화를 시작하는 사건 | push, pull request, 수동 실행 |
| Workflow | 전체 자동화 설명서 | `.github/workflows/ci.yml` |
| Job | 한 가상 PC에서 수행할 작업 묶음 | `test` job |
| Runner | 명령을 실행하는 가상 PC | GitHub의 Ubuntu PC |
| Step | job 안의 한 단계 | Python 설치, pytest 실행 |
| Action | 재사용 가능한 준비된 기능 | checkout, setup-python |
| Artifact | 실행 후 보관하는 결과 파일 | JSON/JUnit/JSONL reports |

이 프로젝트의 GitHub Actions는 다음 순서로 설정돼 있습니다.

1. 코드를 내려받습니다.
2. Python 3.12를 설치합니다.
3. paho-mqtt, PyYAML, pytest를 설치합니다.
4. `docker compose up -d --wait`로 Mosquitto와 Toxiproxy를 시작합니다.
5. pytest와 coverage 80% gate를 실행합니다.
6. 기본 YAML 테스트 10개와 복구 테스트 2개를 실행합니다.
7. 결과 리포트를 artifact로 업로드합니다.
8. 실패했다면 Mosquitto log를 출력합니다.
9. 마지막에 container를 종료합니다.

GitHub 공식 문서에서도 Actions를 repository 안에서 build, test, deployment workflow를 자동 실행하는 CI/CD 플랫폼으로 설명합니다. workflow 파일은 `.github/workflows` 폴더에 있어야 GitHub가 인식합니다.

## 10. 새로운 테스트케이스를 추가하려면

`scenarios/mvp.yaml`의 기존 TC를 참고해 같은 schema로 추가할 수 있습니다.

예를 들어 새로운 QoS 1 상태 메시지를 추가할 수 있습니다.

```yaml
- id: TC-MQTT-011
  requirement_id: REQ-MQTT-DOOR-STATE
  name: Door state message delivery
  kind: publish_receive
  topic: validator/{run_id}/door/state
  qos: 1
  subscribe_qos: 1
  timeout_s: 2.0
  payload:
    message_id: door-{run_id}
    door: DRIVER
    state: LOCKED
  expected:
    received_count: 1
    delivered_qos: 1
    payload_matches: true
```

추가한 뒤 전체 suite를 실행해 `11 passed`인지 확인합니다.

이때 `실제 차량 문을 잠갔다`고 해석하면 안 됩니다. 차량 도메인과 비슷한 JSON 데이터를 MQTT로 전달하는 모의 시험입니다.

## 11. 문제 해결

### `ConnectionRefusedError` 또는 1883 연결 실패

Mosquitto가 실행되지 않은 상태일 가능성이 큽니다.

```powershell
docker compose ps
docker compose up -d --wait
```

### `port 1883 is already in use`

다른 MQTT broker나 이전 container가 1883 포트를 사용 중일 수 있습니다.

```powershell
docker compose down
docker ps
```

어떤 프로그램이 포트를 사용하는지 확인한 뒤 충돌하는 프로그램을 종료해야 합니다.

### pytest에서 integration test가 skipped

기본 설정에서는 broker가 필요한 테스트를 일부러 건너뜁니다. 전체 검사를 하려면 환경변수를 설정합니다.

```powershell
$env:MQTT_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest
```

### failure demo의 종료 코드가 1

정상입니다. 결함을 발견하면 CI도 실패시켜야 하므로 종료 코드 1을 반환합니다.
