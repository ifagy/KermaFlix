Here is the complete `README.md` file fully written in English, along with step-by-step instructions on how to run both `manual_test.py` and `test_all_task1.py`.

Save the following content into a `README.md` file in the root of your project directory:

```markdown
# Kerma Node - Task 1 Implementation

This repository contains the Python implementation of a Kerma Blockchain Node for Task 1 of the *Introduction to Blockchain Technologies* course (WS2026/27)[cite: 1].

The node listens on TCP port `18018`, performs the protocol handshake, handles canonical JSON message serialization/deserialization, manages message defragmentation, and maintains a persistent peer discovery list[cite: 1, 2].

---

## 🛠️ Requirements & Prerequisites

- **Docker & Docker Compose** (via Docker Desktop, OrbStack, or Docker Engine)[cite: 6]
- **Python 3.10+** (installed on your host machine to run local test scripts)

---

## 🚀 Running the Node with Docker

The node is containerized using Docker Compose as required by the automated grading framework[cite: 6].

### 1. Build the Docker Image
```bash
docker compose build

```

### 2. Start the Container in Detached Mode

```bash
docker compose up -d

```

### 3. Monitor Live Logs

```bash
docker compose logs -f

```

### 4. Stop the Container and Clean Up Volumes

```bash
docker compose down -v

```

---

## 🧪 Testing the Node

Make sure your Docker container is actively running (`docker compose up -d`) before executing any tests.

### A. Automated Grader Test Suite (`test_all_task1.py`)

This automated test script validates all requirements specified in Task 1:

1. Handshake execution and immediate `getpeers` request


2. TCP stream defragmentation with artificial delay


3. Rejection of messages sent prior to handshake (`INVALID_HANDSHAKE`)


4. Rejection of a second `hello` message (`INVALID_HANDSHAKE`)


5. Rejection of invalid message formats or versions (`INVALID_FORMAT`)


6. Rejection of syntactically invalid peer addresses (`INVALID_FORMAT`)


7. Persistent peer storage surviving reboots/reconnections


8. Support for multiple simultaneous client connections


9. Correct handling (ignoring) of unknown valid message types without closing the socket



**Run the automated suite:**

```bash
python test_all_task1.py

```

---

### B. Manual Interactive Testing (`manual_test.py`)

To manually inspect responses and test arbitrary JSON messages against your node in real-time:

1. **Launch the manual tester:**
```bash
python manual_test.py

```


2. **Observed Output on Connection:**
   Upon connecting, the node automatically sends its initial `hello` and `getpeers` messages.


3. **Sample Interactive Inputs:**
* **Complete the Handshake:**
```json
{"type":"hello","version":"0.10.0"}

```


* **Request Peer List:**
```json
{"type":"getpeers"}

```


* **Test Invalid Version Error:**
```json
{"type":"hello","version":"5.8.2"}

```


* **Type `exit` to disconnect.**



---

## 📦 Creating the Submission Archive

To build the required `.tar` archive for TUWEL submission (ensuring all files reside directly at the archive root without a enclosing parent folder):

```bash
# Generate submission archive
tar -cvf submission.tar Dockerfile docker-compose.yml protocol.py peer_manager.py connection.py node.py main.py

# Verify archive contents
tar -tf submission.tar

```

```

```