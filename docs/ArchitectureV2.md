# SAGE V2

## Corrected Architecture & Technical Specification

**Project:** SAGE — Strategic Advisory & Guidance Engine
**Architecture:** Modular Monolith
**Runtime:** Always-on local service
**Database:** SQLite
**Primary Interfaces:** Web, CLI, Voice
**Execution Model:** SAGE Core + persistent Worker pool + external Agent Backends

---

# 1. System Definition

SAGE is a personal AI assistant designed to run continuously on the user's machine.

The user communicates with SAGE naturally. SAGE:

1. understands the request,
2. retrieves relevant context,
3. determines how the request should be accomplished,
4. selects direct execution or background execution,
5. performs or delegates the work,
6. verifies the result,
7. handles failures and recovery,
8. remembers relevant information,
9. reports the result to the user.

SAGE remains the authority throughout the entire process.

External agents such as Hermes and OpenCode are execution backends controlled by SAGE.

Workers are execution processes owned and controlled by SAGE.

---

# 2. Core Architectural Principle

```text
                         USER
                          │
              ┌───────────┴───────────┐
              │                       │
             Web                    Voice
              │                       │
             CLI                      │
              └───────────┬───────────┘
                          │
                     SAGE API
                          │
                    SAGE CORE
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
 Conversation        Context           Execution
   Engine             Engine             Engine
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                 ┌────────┴────────┐
                 │                 │
              Direct            Worker
             Execution            Pool
                                   │
                         ┌─────────┼─────────┐
                         │         │         │
                      Worker    Worker    Worker
                         │
                    AgentBackend
                    ┌────┴────┐
                    │         │
                 Hermes    OpenCode
```

SAGE is the authority.

Workers are subordinate execution processes.

Agents are external execution engines.

Tools are capabilities exposed through SAGE's Tool Registry.

---

# 3. Modular Monolith

SAGE V2 remains a modular monolith.

Logical modules:

```text
sage/
├── api/
├── conversation/
├── context/
├── execution/
├── workers/
├── agents/
├── inference/
├── tools/
├── permissions/
├── operations/
├── scheduler/
├── events/
├── memory/
├── notifications/
├── persistence/
├── recovery/
├── observability/
└── voice/
```

These are logical boundaries, not separate services.

Everything initially runs inside the SAGE application except Worker processes.

This avoids the unnecessary complexity of distributed services while keeping boundaries clear enough for future extraction if required.

---

# 4. SAGE Authority Model

The authority hierarchy is:

```text
USER
  ↓
SAGE
  ↓
Worker
  ↓
Tool / Agent / Subprocess
```

The user gives SAGE intent.

SAGE decides how to execute it.

Workers cannot independently:

* ask the user for permission,
* create Workers,
* bypass SAGE,
* change SAGE policy,
* access arbitrary memory,
* directly grant themselves permissions.

Hermes and OpenCode are also controlled through SAGE's AgentBackend interface.

---

# 5. Request Lifecycle

A normal request follows:

```text
USER
 ↓
SAGE API
 ↓
Conversation Engine
 ↓
Context Engine
 ↓
LLM / Decision Layer
 ↓
Execution Policy
 ↓
┌─────────────────────┐
│                     │
Direct              Worker
Execution           Execution
│                     │
└──────────┬──────────┘
           ↓
      Operation Manager
           ↓
       Verification
           ↓
      Result Handling
           ↓
       Conversation
           ↓
          USER
```

The LLM interprets the request.

Deterministic SAGE policy decides what may actually happen.

The LLM never has final authority over permissions or destructive actions.

---

# 6. Direct Execution vs Worker Execution

SAGE handles small atomic operations directly.

Examples:

```text
"What time is it?"
"What's the weather?"
"Set a reminder for 6 PM."
"Calculate 17 × 38."
```

A Worker handles meaningful execution tasks.

Examples:

```text
"Summarize this webpage."
"Research German universities."
"Process my inbox."
"Analyze this repository."
"Modify this project and run its tests."
```

The distinction is not simply duration.

A task becomes Worker-worthy when it involves substantial:

* external I/O,
* multiple operations,
* autonomous iteration,
* tool usage,
* research,
* file manipulation,
* code execution,
* long-running work,
* or independent execution state.

---

# 7. Persistent Worker Pool

SAGE maintains a persistent pool of Workers.

Default configuration:

```text
Minimum Workers: 10
Maximum Workers: configurable
```

Example:

```text
Alpha
Bravo
Charlie
Delta
Echo
Foxtrot
Golf
Hotel
India
Juliet
```

Workers remain alive while idle.

An idle Worker should remain lightweight.

Workers must **not** automatically load:

* large local models,
* browsers,
* unnecessary runtimes,
* project-specific environments,
* expensive resources.

Heavy resources are acquired only when needed.

SAGE may dynamically create additional Workers when all existing Workers are occupied.

A configurable hard maximum prevents runaway process creation.

---

# 8. Worker Identity vs Task Identity

A critical distinction:

```text
Worker
  │
  ├── persistent process identity
  │
  └── Task Execution Context
          │
          └── disposable
```

Worker `Alpha` can execute:

```text
Task 101
```

then later:

```text
Task 145
```

But Task 145 must never inherit Task 101's execution state accidentally.

Worker identity persists.

Task context does not.

---

# 9. Worker Task Reset Boundary

Every Worker follows:

```text
IDLE
 ↓
ASSIGNED
 ↓
STARTING
 ↓
RUNNING
 ↓
VERIFYING
 ↓
FINALIZING
 ↓
CLEANUP
 ↓
READY
```

Before becoming `READY`, the Worker performs cleanup.

Cleanup includes, where applicable:

* conversation/task context,
* temporary files,
* environment variables,
* credentials,
* browser contexts,
* subprocesses,
* open file handles,
* network connections,
* agent bindings,
* temporary caches,
* task-specific memory.

The Worker must pass a health/reset check before accepting another task.

If cleanup fails:

```text
CLEANUP_FAILED
```

The Worker is removed from the available pool and SAGE can terminate/recreate it.

---

# 10. Worker Task Contract

SAGE creates a task containing:

```text
task_id
objective
context
allowed_tools
resource_limits
working_directory
agent_backend
agent_session
deadline
permission_scope
```

The Worker does not receive SAGE's entire memory.

It receives only the context required for the assigned task.

The Worker returns structured execution information and the full task result.

---

# 11. Worker Communication

Worker ↔ SAGE communication uses Unix Domain Sockets.

Messages are structured and versioned.

Every message contains:

```text
protocol_version
message_type
event_id
task_id
worker_id
sequence_number
timestamp
payload
```

Example:

```json
{
  "protocol_version": 1,
  "message_type": "PROGRESS",
  "event_id": "evt_82a1",
  "task_id": "task_91",
  "worker_id": "alpha",
  "sequence_number": 14,
  "payload": {
    "message": "Running repository tests"
  }
}
```

The protocol supports:

* framing,
* maximum message size,
* schema validation,
* request IDs,
* sequence numbers,
* backpressure,
* reconnect,
* protocol versioning.

---

# 12. Task State Machine

Tasks use explicit states:

```text
QUEUED
ASSIGNED
STARTING
RUNNING
CHECKPOINTING
VERIFYING
COMPLETING
COMPLETED

FAILED
RETRYING

CANCELLING
CANCELLED
CANCEL_FAILED

RECOVERY_PENDING
UNKNOWN
```

`UNKNOWN` is important.

It means SAGE cannot safely determine whether an external operation succeeded.

SAGE must not blindly retry an `UNKNOWN` operation.

It must reconcile the external state first.

---

# 13. Task Leases

Every running Worker task has a lease.

Example:

```text
task_id: task_91
worker_id: alpha
lease_until: 14:32:10
```

The Worker periodically renews the lease.

If SAGE crashes:

```text
SAGE restarts
 ↓
find expired leases
 ↓
inspect task state
 ↓
determine recovery strategy
```

Possible outcomes:

```text
RESUME
RETRY
RECONCILE
CANCEL
MARK UNKNOWN
```

This prevents a stale Worker from being mistaken for an active Worker.

---

# 14. Atomic Task Claiming

Two Workers must never accidentally claim the same task.

Task assignment uses an atomic SQLite transaction.

Conceptually:

```text
BEGIN TRANSACTION

find eligible task

verify task is still QUEUED

assign Worker

set lease

COMMIT
```

Only after the transaction succeeds does the Worker begin execution.

This prevents duplicate task claims.

---

# 15. Operation Manager

The most important addition to V2 is the **Operation Manager**.

A task and a real-world side effect are not the same thing.

Example:

```text
Task:
"Email John the report."

Operation:
"Send email message X."
```

The Operation Manager tracks real-world side effects.

Every side-effecting operation gets an ID:

```text
operation_id = op_83af91
```

Examples:

```text
send_email
delete_file
git_push
create_record
send_message
make_API_mutation
purchase
move_file
```

---

# 16. Idempotency and Reconciliation

Before performing an external side effect:

```text
Task
 ↓
Operation Manager
 ↓
check operation state
 ↓
execute
 ↓
record result
```

Operation states:

```text
PENDING
EXECUTING
SUCCEEDED
FAILED
UNKNOWN
RECONCILING
```

Example failure:

```text
Worker sends email
        ↓
Email provider accepts it
        ↓
Worker crashes
        ↓
SAGE restarts
        ↓
Operation = UNKNOWN
```

SAGE does **not** simply resend the email.

Instead:

```text
UNKNOWN
 ↓
reconcile provider
 ↓
already sent?
 ├── YES → SUCCEEDED
 └── NO  → safe retry
```

Where providers support idempotency keys, SAGE supplies them.

Where they do not, SAGE uses reconciliation mechanisms where possible.

For inherently non-idempotent actions with no reconciliation mechanism, SAGE must surface the uncertainty rather than pretending the result is known.

---

# 17. Task vs Operation

These are deliberately separate:

```text
Task
 ├── Operation A
 ├── Operation B
 └── Operation C
```

A task can succeed even if an individual operation is retried.

An operation can remain `UNKNOWN` even if its parent task crashes.

This separation makes crash recovery substantially safer.

---

# 18. AgentBackend

External agents use a common interface:

```text
AgentBackend
├── OpenCodeBackend
└── HermesBackend
```

Required capabilities:

```text
start()
provide_task()
provide_context()
status()
events()
cancel()
reconnect()
recover()
result()
```

SAGE does not reproduce their internal agent loops.

---

# 19. Agent Session Isolation

Worker reuse does not imply agent-session reuse.

Explicit relationship:

```text
Task
 └── Worker
      └── Agent Session
```

A future task may reuse an existing agent session only when SAGE explicitly determines that it belongs to the same:

* project,
* user intent,
* execution context,
* resource scope.

Otherwise a new session is created.

No implicit session reuse.

---

# 20. Browser Isolation

Browser state is task-scoped by default.

A Worker must not accidentally carry:

* cookies,
* local storage,
* login state,
* browser tabs,
* downloads,
* site sessions

from one unrelated task into another.

Default:

```text
Task A → Browser Context A
Task B → Browser Context B
```

Persistent browser sessions are allowed only when deliberately associated with a project/account/session.

---

# 21. Centralized Inference Gateway

Workers do not directly manage model-provider credentials.

Instead:

```text
Worker
   ↓
SAGE Inference Manager
   ↓
Model Router
   ↓
Provider Adapter
 ┌──────┬───────┐
NIM  Nebius   Local
```

The Inference Manager controls:

* provider credentials,
* model selection,
* capability checks,
* budgets,
* rate limits,
* fallback,
* latency tracking,
* token accounting,
* provider health,
* streaming normalization.

This keeps inference authority inside SAGE.

---

# 22. Model Capability Matrix

Models are described using capabilities rather than only names.

Example:

```text
model_id
provider
context_window
reasoning
tool_calling
structured_output
vision
streaming
latency_class
cost
availability
```

A fallback model must satisfy the task's required capabilities.

For example:

```text
Task requires:
tool_calling = true
vision = true
context >= 32k
```

SAGE cannot fallback to a model lacking those capabilities simply because it is available.

---

# 23. Provider Adapter

Provider responses are normalized:

```text
Provider API
 ↓
Provider Adapter
 ↓
Normalized Inference Response
 ↓
SAGE
```

SAGE never executes raw provider output.

Tool calls pass through:

```text
LLM
 ↓
Provider Adapter
 ↓
Normalized Tool Call
 ↓
Schema Validator
 ↓
Permission Engine
 ↓
Tool Registry
 ↓
Operation Manager
 ↓
Tool
```

---

# 24. Inference Metrics

SAGE records:

```text
TTFT
total latency
queue latency
provider latency
tokens/sec
input tokens
output tokens
error rate
timeouts
429 rate
fallback count
estimated cost
```

TTFT and total completion latency are tracked separately.

This matters because a model can begin responding quickly but take a long time to finish.

---

# 25. Inference Fallback

Basic policy:

```text
primary model
 ↓
retry once if appropriate
 ↓
compatible fallback
```

Fallback must consider:

* required capabilities,
* context size,
* tool support,
* vision,
* structured output,
* latency requirements,
* provider availability.

Streaming requires special handling.

If a provider fails before meaningful output is delivered, fallback can occur normally.

If a provider fails after substantial output has already been streamed, SAGE must not silently concatenate an unrelated response.

It should either continue/recover explicitly or report the interruption.

---

# 26. Permission Architecture

Tools contain deterministic metadata:

```text
destructive: true/false
permission_scope
worker_allowed
```

The LLM cannot declare an action safe.

SAGE determines this from Tool Registry metadata.

Permission states:

```text
ALLOWED
APPROVAL_REQUIRED
DENIED
```

---

# 27. Approval Binding

Approval is never simply:

```text
approved = true
```

Approval is bound to:

```text
task_id
worker_id
operation
resource
action
scope
expiration
constraints
```

Example:

```text
Task: task_91
Worker: alpha
Action: delete
Resource: /home/aman/project-x/*
Expires: 15:30
```

Before execution, SAGE revalidates the approval.

This prevents stale approvals from being reused for unrelated actions.

---

# 28. Protected Resources

Application permissions are not treated as a complete OS security boundary.

Protected system resources remain protected.

Defense in depth includes:

* application permission checks,
* process isolation,
* OS permissions,
* filesystem permissions,
* sandboxing where practical,
* restricted credentials.

---

# 29. Tool Registry

All tools pass through one registry.

Each tool defines:

```text
name
description
input_schema
output_schema
permission_requirements
destructive
worker_eligible
handler
```

Workers can receive a task-specific tool allowlist.

```text
allowed_tools
```

is an actual execution boundary.

It is not merely information placed in the prompt.

If a Worker attempts an unavailable tool call:

```text
DENIED_BY_TOOL_POLICY
```

---

# 30. Memory Architecture

SAGE memory consists of:

```text
RAW HISTORY
LONG-TERM MEMORY
CURRENT STATE
CONTEXT ENGINE
```

### Raw History

Permanent source material.

### Long-Term Memory

Useful persistent facts:

* preferences,
* projects,
* goals,
* decisions,
* relationships,
* habits,
* important events.

### Current State

Regenerated periodically.

Contains:

* active projects,
* active tasks,
* recent important activity,
* upcoming events,
* current goals,
* active Workers,
* current priorities.

It is not simply "the last 24 hours."

---

# 31. Memory Provenance

Every extracted memory records provenance.

Example:

```text
memory_id
content
source_message_id
created_at
confidence
explicit_or_inferred
last_confirmed
status
```

Explicit user statements receive stronger provenance than inferred information.

Speculative LLM inference must not silently become an unquestioned permanent fact.

High-impact uncertain information should require confirmation before being treated as authoritative.

---

# 32. Memory Evolution

If the user says:

> "I use Fedora."

Later:

> "I switched to Ubuntu."

SAGE does not create two contradictory active facts.

Instead:

```text
Fedora
 ↓
SUPERSEDED

Ubuntu
 ↓
ACTIVE
```

Original conversation history remains unchanged.

---

# 33. Forgetting

When the user asks SAGE to forget something:

```text
ACTIVE MEMORY
 ↓
FORGOTTEN
```

The memory is removed from normal retrieval.

The original conversation remains intact because conversation history is separate from active memory.

The audit record contains only the memory identifier and operation metadata necessary to record that the action occurred, rather than retaining the forgotten content in the audit trail.

---

# 34. Context Engine

Every meaningful LLM call goes through the Context Engine.

```text
Request
 ↓
Context Engine
 ├── current conversation
 ├── Current State
 ├── long-term memory
 ├── relevant history
 ├── active tasks
 ├── Workers
 └── project state
 ↓
Context Builder
 ↓
LLM
```

Retrieval uses:

```text
SQLite FTS5
+
embeddings
+
deterministic relevance signals
```

No vector database initially.

---

# 35. Context Budgets

Every LLM call has a bounded context budget.

The Context Engine may retrieve many candidates.

The Context Builder chooses the useful subset.

Different models/tasks can have different budgets.

This prevents context growth from becoming an uncontrolled cost/performance problem.

---

# 36. Scheduler and Event System

SAGE owns:

* reminders,
* recurring jobs,
* cron-like schedules,
* proactive routines,
* scheduled Workers,
* event-triggered tasks.

The operating system's job is primarily to keep SAGE alive.

---

# 37. Event Architecture

Events are structured:

```text
event_id
event_type
source
timestamp
sequence_number
payload
```

Examples:

```text
email_received
worker_completed
worker_failed
system_started
network_restored
file_changed
scheduled_time_reached
```

Events are asynchronous by default.

SAGE decides whether to:

```text
IGNORE
ACT
NOTIFY
CREATE_TASK
```

---

# 38. Event Deduplication

Event handlers must be idempotent.

If:

```text
event_123
```

is received twice, SAGE must not perform the resulting action twice.

Events therefore have unique IDs.

Handlers track processed events where durable deduplication is required.

---

# 39. Event Ordering

Events may arrive out of order.

Per-task/worker sequence numbers are used:

```text
101 STARTED
102 PROGRESS
103 TOOL_USED
104 COMPLETED
```

If event 102 arrives after 104, the consumer can identify it as stale.

---

# 40. WebSocket Architecture

WebSocket is a live event channel.

It is **not** the source of truth.

When the UI connects:

```text
GET initial state
       ↓
receive snapshot
       ↓
subscribe to events
```

If disconnected:

```text
reconnect
 ↓
refetch authoritative state
 ↓
resume live events
```

The UI never assumes a missed WebSocket event means the underlying state was lost.

---

# 41. API Contract

Normal request:

```text
POST /conversation/message
```

returns quickly.

Example:

```json
{
  "message_id": "msg_123",
  "interaction_id": "int_456",
  "task_id": "task_789"
}
```

The API does not hold an HTTP connection open for a 30-minute task.

Live progress is delivered through WebSocket events.

---

# 42. Identifier Model

SAGE uses distinct identifiers:

```text
request_id
conversation_id
message_id
interaction_id
task_id
worker_id
operation_id
agent_session_id
event_id
```

These are not interchangeable.

They allow a complete execution trace:

```text
request
 ↓
conversation
 ↓
interaction
 ↓
task
 ↓
worker
 ↓
operation
 ↓
tool/provider
 ↓
event
```

---

# 43. Correlation IDs

Every internal operation propagates relevant IDs.

Example:

```text
request_id=req_1
task_id=task_5
worker_id=alpha
operation_id=op_8
provider_request_id=prov_12
```

This allows SAGE to answer:

> What happened to the request I made five minutes ago?

without relying on textual log searching.

---

# 44. Cancellation

Cancellation follows:

```text
USER
 ↓
SAGE
 ↓
Worker
 ↓
Agent
 ↓
Subprocess
```

State:

```text
RUNNING
 ↓
CANCELLING
 ↓
CANCELLED
```

If graceful cancellation fails:

```text
CANCELLING
 ↓
grace period
 ↓
force termination
```

If the outcome of an external operation cannot be determined:

```text
UNKNOWN
```

Cancellation does not falsely imply that an already-submitted external action was reversed.

---

# 45. Process Tree Management

Workers can launch:

* Hermes,
* OpenCode,
* shell commands,
* compilers,
* browsers,
* other subprocesses.

SAGE tracks process trees where possible.

Cancellation must propagate to the entire task process group rather than killing only the top-level process.

---

# 46. Failure Recovery

Recoverable failures are retried within bounded limits.

Example:

```text
API unavailable
 ↓
retry
 ↓
still unavailable
 ↓
compatible fallback if available
 ↓
otherwise report failure
```

Retries are never infinite.

Each retry records:

```text
attempt
reason
delay
provider
result
```

---

# 47. Crash Recovery

On startup:

```text
SAGE START
 ↓
load database
 ↓
recover scheduler
 ↓
recover Workers
 ↓
inspect expired leases
 ↓
inspect incomplete operations
 ↓
reconcile UNKNOWN operations
 ↓
recover resumable tasks
 ↓
resume normal operation
```

SAGE does not simply restart every interrupted task.

It evaluates its durable state first.

---

# 48. SQLite Strategy

SQLite remains the system of record.

Use:

```text
WAL mode
busy timeout
short transactions
connection management
```

Important state transitions are persisted immediately.

High-frequency ephemeral state does not need a database write every time.

For example:

```text
Worker heartbeat
```

can primarily remain in memory.

A durable checkpoint can be written periodically.

---

# 49. Persistence Model

One SQLite database can contain:

```text
conversations
messages
memories
memory_events
current_state
tasks
task_events
workers
worker_events
operations
permissions
approvals
agent_sessions
events
scheduled_jobs
artifacts
audit_records
inference_usage
```

Logical separation is maintained through tables and module ownership.

---

# 50. Logs vs Audit

Operational logs are optimized for debugging.

Audit records are optimized for accountability.

Operational logs may contain:

```text
request_id
task_id
worker_id
provider
latency
status
error_type
```

They should not automatically contain complete:

* prompts,
* responses,
* passwords,
* API payloads,
* private documents.

Audit records cover significant actions such as:

* permission changes,
* destructive operations,
* memory changes,
* configuration changes,
* credential access.

Audit retention is stronger than ordinary debug-log retention.

---

# 51. Artifacts

Large tool outputs should not necessarily be duplicated into:

* task records,
* messages,
* logs,
* memory.

Instead:

```text
Task
 ↓
Artifact
 ↓
artifact_id
```

SAGE stores metadata and references where possible.

Examples:

```text
report.pdf
repository analysis
large webpage
generated dataset
tool output
```

This prevents the SQLite database from unnecessarily exploding in size.

---

# 52. Fair Worker Scheduling

There is no explicit priority hierarchy.

However, fairness still exists.

SAGE should prevent one source from monopolizing all Workers.

For example:

```text
User tasks
Scheduled tasks
Proactive tasks
Recovery tasks
```

should not allow an endless stream from one category to starve everything else.

Initially, simple fair/FIFO scheduling is sufficient.

---

# 53. Resource Management

Workers have resource constraints.

SAGE tracks:

```text
CPU
RAM
process count
runtime
disk usage
network usage
```

Where practical, enforcement uses operating-system mechanisms rather than database fields alone.

On Linux this can eventually include:

* process groups,
* resource limits,
* cgroups,
* timeouts.

The architecture does not assume that recording:

```text
max_memory = 2GB
```

actually enforces 2 GB.

---

# 54. Dynamic Worker Scaling

Default:

```text
10 Workers
```

If all are occupied:

```text
SAGE
 ↓
check max_workers
 ↓
create additional Worker
```

Example:

```text
10 → 11 → 12 → ...
```

until:

```text
MAX_WORKERS
```

is reached.

Workers are removed when they become unhealthy or when the system later implements controlled scale-down.

---

# 55. Notifications

SAGE can notify through:

```text
Web UI
Native notifications
Voice
Telegram
WhatsApp
CLI
```

Channels are independent.

Not every event needs to reach every channel.

For example:

```text
Worker progress
→ Web UI

Important completed task
→ Web + native notification

Urgent event while voice mode active
→ Voice
```

---

# 56. Proactive Behavior

SAGE is proactive.

Example:

```text
05:30
 ↓
Scheduler
 ↓
SAGE checks email
 ↓
Context/importance analysis
 ↓
important?
 ├── NO → store state
 └── YES → notify user
```

The scheduler only triggers the check.

SAGE decides whether the result deserves action.

---

# 57. Voice Architecture

**Full-duplex WebRTC with token-level streaming synthesis.**

Pipeline:
```text
Microphone
  ↓
WebRTC (Opus, 48kHz, echo cancellation)
  ↓
Silero VAD (streaming, 30ms frames, noise suppression ON)
  ↓
┌──────────────────────────────────────────────┐
│  Barge-In Detection (parallel path)          │
│  • VAD triggers during TTS playback          │
│  • Immediate TTS cancel + context flush      │
│  • User audio buffered for ASR               │
└──────────────────────────────────────────────┘
  ↓
Streaming ASR (Whisper.cpp / faster-whisper, token-level)
  ↓
SAGE (conversation engine)
  ↓
Streaming LLM (token-level)
  ↓
Token-level Streaming TTS (Chatterbox / Kokoro / Piper)
  ↓
WebRTC audio track (Opus) → Speaker
```

Key properties:
- **Full-duplex**: WebRTC handles simultaneous send/receive; no half-duplex turn-taking
- **Token-level streaming**: TTS starts at first token, not sentence boundary — <100ms first-audio latency
- **Barge-in**: Silero VAD runs continuously during TTS playback; detection → immediate TTS stop + LLM cancel + ASR flush
- **Noise suppression**: RNNoise / WebRTC NS integrated in VAD front-end
- **Echo cancellation**: WebRTC AEC3 for full-duplex without feedback

Voice is another client of SAGE, not a separate intelligence system.

---

# 58. Voice Barge-In (Detailed)

```text
TTS playing (token stream)
  ↓
Silero VAD (continuous, 30ms frames)
  ↓
Speech detected during playback
  ├─→ Cancel TTS stream immediately (WebRTC track stop)
  ├─→ Send cancellation to LLM (abort generation)
  ├─→ Flush ASR buffer (start fresh from barge-in point)
  └─→ User audio → ASR → SAGE (new turn)
```

- VAD threshold tuned for barge-in sensitivity (lower than idle VAD)
- Minimum barge-in audio: ~300ms before action
- TTS cancellation propagates to token stream (no wasted compute)

---

# 59. Streaming Voice (Token-Level)

```text
LLM (streaming tokens)
  ↓
Token buffer (accumulate ~10-20 tokens / phoneme chunk)
  ↓
Streaming TTS (Chatterbox / Kokoro — supports incremental synthesis)
  ↓
Audio chunks → WebRTC send track (Opus, 20ms frames)
  ↓
Speaker (browser / native client)
```

- First audio chunk at ~80-120ms after first LLM token
- No sentence-boundary wait — phoneme-level chunking
- Backpressure: TTS slows if WebRTC send buffer full
- Fallback: if streaming TTS unavailable, fall back to sentence-level

---

# 60. Verification

Verification is proportional to task complexity.

Tiny deterministic action:

```text
Set reminder
 ↓
database confirms creation
 ↓
done
```

No separate verifier LLM is required.

Complex task:

```text
Worker result
 ↓
deterministic checks
 ↓
LLM verifier if necessary
 ↓
accepted / retry / report failure
```

Verification checks actual outcomes rather than merely trusting the Worker.

---

# 61. Verification Examples

For file operation:

```text
Was file created?
Does it exist?
Is content valid?
```

For API request:

```text
Was request accepted?
What is the resulting resource state?
```

For coding:

```text
Did files change?
Did tests run?
Did tests pass?
Does requested behavior exist?
```

For research:

```text
Were requested questions answered?
Are sources present?
Is the answer complete enough?
```

---

# 62. SAGE Result Handling

The Worker returns raw execution results.

SAGE then:

1. verifies,
2. interprets,
3. organizes,
4. summarizes,
5. adapts the response to the current conversation.

Workers do not decide the final conversational presentation.

---

# 63. Conversation Continuity

The user experiences one continuous SAGE conversation.

Internally, SAGE can segment and retrieve context.

The user does not need to manually start a new conversation every day.

Historical conversations remain accessible through the Context Engine.

---

# 64. Current State

Current State is a derived representation of what matters now.

Example:

```text
Active Project:
SAGE V2

Status:
Architecture finalization

Active Workers:
Alpha - repository analysis
Bravo - idle

Upcoming:
Internship onboarding

Recent Changes:
Worker architecture revised
```

Completed projects eventually move out of active state while remaining in historical memory.

---

# 65. Scheduler Recovery

Scheduled jobs also have durable identities.

A scheduled trigger receives an event ID.

If SAGE restarts around the trigger time, it checks:

```text
Did this scheduled occurrence already execute?
```

before creating the task.

This prevents duplicate scheduled execution.

---

# 66. Secrets

Provider/API credentials are controlled by SAGE.

Workers receive only the credentials or capability required for their current task.

Workers should not have unrestricted access to the entire secret store.

Secrets should not appear in ordinary logs.

---

# 67. Startup Sequence

```text
Machine boots
 ↓
SAGE service starts
 ↓
load configuration
 ↓
open SQLite
 ↓
initialize modules
 ↓
recover tasks
 ↓
recover operations
 ↓
recover scheduler
 ↓
start Worker pool
 ↓
health-check Workers
 ↓
start API
 ↓
start event system
 ↓
SAGE READY
```

---

# 68. Shutdown

Graceful shutdown:

```text
STOP ACCEPTING NEW WORK
 ↓
finish/ checkpoint active operations
 ↓
request Worker cancellation where appropriate
 ↓
persist task state
 ↓
close agent sessions
 ↓
close Worker connections
 ↓
flush important state
 ↓
shutdown
```

The goal is to leave enough durable information for safe recovery.

---

# 69. Core Execution Model

The corrected model is:

```text
                  ┌───────────────────────┐
                  │         SAGE          │
                  │                       │
USER ────────────►│ Conversation           │
                  │ Context                │
                  │ Decision               │
                  │ Permissions            │
                  │ Operations             │
                  │ Memory                 │
                  │ Scheduler              │
                  │ Inference              │
                  │ Verification           │
                  └──────────┬────────────┘
                             │
                    ┌────────┴─────────┐
                    │                  │
                Direct             Worker Pool
                Action                 │
                                      │
                         ┌────────────┼────────────┐
                         │            │            │
                      Alpha        Bravo       Charlie
                         │
                    AgentBackend
                    ┌────┴─────┐
                    │          │
                 Hermes     OpenCode
```

---

# 70. Critical Safety Boundaries

The following boundaries are mandatory:

### Boundary 1 — User authority

```text
User → SAGE
```

### Boundary 2 — Worker authority

```text
SAGE → Worker
```

Workers cannot elevate themselves.

### Boundary 3 — Tool authority

```text
Model → Tool Proposal
SAGE → Permission + Validation
Tool → Execution
```

### Boundary 4 — Side effects

```text
Task → Operation Manager → External Side Effect
```

### Boundary 5 — Inference

```text
Worker → Inference Manager → Provider
```

### Boundary 6 — Recovery

```text
Crash → Durable State → Reconciliation
```

These boundaries prevent the majority of dangerous failure modes identified during review.

---

# 71. What Was Explicitly Fixed

The architecture review identified several weaknesses. They are now addressed as follows:

| Problem                                | V2 Correction                                       |
| -------------------------------------- | --------------------------------------------------- |
| Duplicate external actions after crash | Operation Manager + operation IDs + reconciliation  |
| Worker state leakage                   | Explicit cleanup/reset boundary                     |
| 10 Workers potentially expensive       | Lightweight idle Workers; heavy resources on demand |
| Workers owning inference credentials   | Central SAGE Inference Manager                      |
| Provider APIs differ                   | Provider adapters + capability matrix               |
| Raw model tool calls                   | Normalization + validation                          |
| Stale approvals                        | Scoped, expiring, task-bound approvals              |
| Heartbeat mistaken for task safety     | Separate health/task/operation state                |
| Weak task states                       | Expanded state machine + `UNKNOWN`                  |
| Duplicate task claims                  | Atomic SQLite claiming                              |
| Stale Workers after crash              | Task leases                                         |
| Agent session leakage                  | Explicit task/session binding                       |
| Browser leakage                        | Task-scoped browser contexts                        |
| Worker tools too broad                 | Hard `allowed_tools` boundary                       |
| Duplicate scheduled events             | Event IDs + idempotent handlers                     |
| Out-of-order events                    | Sequence numbers                                    |
| WebSocket state loss                   | Snapshot + event stream                             |
| SQLite locking                         | WAL + short transactions                            |
| Excessive heartbeat writes             | Ephemeral health + durable checkpoints              |
| Huge/unsafe logs                       | Metadata-first logging                              |
| Audit mixed with logs                  | Separate audit records                              |
| Memory hallucinated as fact            | Provenance + explicit/inferred metadata             |
| LLM deciding destructive status        | Tool metadata determines it                         |
| Cancellation not immediate             | Grace period + force termination                    |
| Resource limits not enforced           | OS-level enforcement where needed                   |
| Worker explosion                       | Configurable maximum                                |
| Worker starvation                      | Fair scheduling                                     |
| Poor inference latency visibility      | TTFT + completion + queue/provider metrics          |
| Invalid provider fallback              | Capability-aware routing                            |
| Broken streaming fallback              | Explicit streaming failure handling                 |
| Voice interruption missing             | Barge-in architecture                               |
| Slow voice response                    | Streaming LLM → TTS                                 |
| HTTP held for long tasks               | Async interaction/task IDs                          |
| Difficult debugging                    | Correlation IDs throughout system                   |

---

# 72. Final Architecture Philosophy

SAGE V2 deliberately avoids unnecessary distributed infrastructure.

It uses:

```text
SQLite
Modular Monolith
Unix Domain Sockets
Persistent Workers
In-process Event System
Centralized Inference
Deterministic Permissions
Operation Tracking
Durable Task State
```

rather than immediately introducing:

```text
Redis
Kafka
RabbitMQ
Kubernetes
microservices
distributed databases
workflow orchestration platforms
```

The architecture is intentionally simple at the infrastructure level while being explicit about the places where correctness actually matters.

---

# 73. Final Mental Model

The cleanest way to think about SAGE is:

```text
                     SAGE
               ┌──────────────┐
               │ Intelligence │
               │ Authority    │
               │ Memory       │
               │ Policy       │
               │ Scheduling   │
               │ Inference    │
               │ Verification │
               └──────┬───────┘
                      │
             ┌────────┴────────┐
             │                 │
          Direct            Workers
          Actions          ┌───────┐
                            │ Alpha │
                            │ Bravo │
                            │ ...   │
                            └───┬───┘
                                │
                         Agent / Tools
                                │
                    ┌───────────┴───────────┐
                    │                       │
                  Hermes                 OpenCode
```

SAGE is the authority.

Workers are its execution workforce.

Hermes and OpenCode are specialized external execution engines.

Tools are capabilities.

The Operation Manager protects real-world side effects.

The Inference Manager controls model execution.

SQLite provides durable truth.

The Event System connects components.

The Context Engine gives SAGE the information it needs.

The user remains the ultimate authority.
