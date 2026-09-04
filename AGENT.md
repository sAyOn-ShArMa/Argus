# Argus project specification

## Mission

Argus is a modular personal AI assistant for one primary user. It begins as a safe, inexpensive laptop application and may later become a central service for text, voice, memory, computer automation, vision, robotics, IoT, and proactive assistance.

The architecture must leave room for additional authorized users with separate profiles, permissions, preferences, and access levels without adding multi-user complexity to the first version.

## Personality

Argus is calm, intelligent, professional, confident, concise, slightly witty, and helpful without being overly talkative. It normally gives short, useful answers and expands for lessons, explanations, technical breakdowns, or detailed requests.

## Capability priorities

1. **AI conversation and assistance:** general questions, studying, coding, robotics, research, brainstorming, problem-solving, and project planning.
2. **Computer control and automation:** applications, files, media, system information, approved commands, and repetitive tasks.
3. **Personal assistance:** reminders, tasks, deadlines, calendar events, notifications, searches, project information, and eventually messages and connected devices.

Future layers may add computer vision, smart-home integration, Arduino, ESP32, sensors, robots, and remote devices.

## Technical direction

- Python is the primary language.
- Prefer free or local technology. The initial model provider is configurable and must sit behind a narrow provider interface.
- The initial deployment is laptop-first, with clean boundaries for a future always-on server and device clients.
- Configuration is stored in human-readable JSON. Secrets are never stored in tracked source files.
- Text remains a permanent interface even after voice is added.

## Interface sequence

1. Typed text
2. Push-to-talk
3. Wake word: "Argus"
4. Optional always-listening mode

Voice should eventually be calm, sophisticated, clear, confident, moderately deep, futuristic, and natural. Prefer local speech technology such as Whisper or Faster-Whisper for transcription and Piper or system speech for synthesis.

## Safety boundaries

Argus requires explicit, per-action confirmation before sending messages or posts, spending money, purchasing anything, permanently deleting data, installing or uninstalling software, changing important or security settings, sharing private information, running destructive commands, changing account security, controlling dangerous hardware, or doing anything difficult to reverse.

Low-risk actions such as opening an application, reading an approved file, checking system information, searching, playing media, opening a folder, or reporting time may normally proceed without confirmation once those capabilities exist.

## Proactivity

Future proactive behavior must be quiet by default, respect quiet hours and notification categories, avoid duplicate warnings, support priorities, and interrupt only when sufficiently important.

## Verified milestone: Phase 1

Build and independently verify:

- Python project structure
- Streaming typed conversation
- Provider-independent AI connection
- Safe built-in command routing
- Process-local conversation context
- Editable JSON configuration
- Clear failure handling and secret handling

The user verified Phase 1 in a live conversation on 2026-08-15.

## Verified milestone: Phase 2

Build and independently verify:

- Provider-independent local tool registry
- Allowlisted application and website opening
- File search restricted to explicitly approved folders
- Read-only system information
- Finite Windows media controls
- Allowlisted command execution without a shell
- Explicit, fresh confirmation before every command execution

Phase 2 must not add file deletion, application closing, arbitrary shell access,
durable memory, messaging, purchases, installations, security changes, hardware
control, or proactive behavior.

The user verified Phase 2 in a live conversation on 2026-08-15.

## Verified milestone: Phase 3

Build and independently verify:

- Explicit one-turn push-to-talk microphone input
- Bounded in-memory WAV capture with no background listening
- Provider-independent speech recognition interface
- Groq Whisper transcription using the existing credential
- Provider-independent speech synthesis interface
- Local Windows text-to-speech with a preferred calm male installed voice
- Typed mode that remains usable when voice setup fails

Phase 3 must not add a wake word, continuous listening, stored recordings,
durable memory, messaging, purchases, deletion, installations, security changes,
hardware control, or proactive behavior.

The user verified Phase 3 in a live conversation on 2026-08-15.

## Verified milestone: Phase 4

Build and independently verify:

- Local recognition of the wake phrase "Argus"
- Visible, opt-in wake mode entered with `/wake`
- Automatic hands-free command capture after activation
- Local acknowledgement and spoken response
- Clean microphone handoff between wake detection and command capture
- Immediate return to typed mode with Ctrl+C or a spoken stop command

Wake mode must never start invisibly or automatically. Wake audio remains local;
only the follow-up command recording is sent to the configured transcription
provider. Phase 4 must not add durable memory, messaging, purchases, deletion,
installations, security changes, hardware control, or proactive behavior.

The user verified Phase 4 in a live conversation on 2026-08-15.

## Verified milestone: Phase 5

Build and independently verify:

- Profile-scoped local SQLite storage
- Durable completed conversation turns with a bounded startup context window
- Explicit facts, preferences, and project memories that can be viewed
- Local tasks and stored reminders
- Provider-neutral memory tools with confirmation before persistent writes
- No model-controlled permanent deletion
- Explicit commands to inspect records and confirmed permanent deletion commands
- `/reset` that starts fresh context without silently erasing stored history

The database must remain in local application data by default, outside synced
project folders, and be ignored by version control if a project-local override is
used. Active context and tool-recalled memories may be sent to the configured
model provider for a reply, and this must be disclosed. Phase 5 reminders are
storage only: they must not claim to notify proactively. Phase 5 must not add
messaging, purchases, general file deletion, installations, security changes,
hardware control, or proactive behavior.

The user verified Phase 5 in a live conversation on 2026-08-16.

## Verified milestone: Phase 6

Build and independently verify:

- Explicit, one-frame webcam capture with a visible terminal indicator
- Local image-file analysis restricted to approved folders
- Local object detection with labels, confidence, and bounding boxes
- Local QR-code detection and decoding
- Anonymous face detection without identity recognition
- Local hand-gesture recognition for supported canned gestures
- A provider-independent vision interface and configuration
- No background camera access, saved frames, or cloud image upload

Camera access must be user initiated or freshly confirmed. Image processing is
local by default, captured webcam frames are discarded after analysis, and no
biometric identity database may be created in Phase 6. Phase 6 must not add
messaging, purchases, deletion, installations beyond its declared vision
dependencies, security changes, hardware control, or proactive behavior.

The user verified Phase 6 in a live conversation on 2026-08-16.

## Verified milestone: Phase 7

Build and independently verify:

- A provider-independent robotics and IoT device interface
- A deterministic simulated robot that works without physical hardware
- Explicitly allowlisted Arduino/ESP32 serial devices and telemetry channels
- Bounded device status and one-shot sensor telemetry reads
- Strict, finite LED, servo, and motor command names and value ranges
- Fresh confirmation before every actuator write
- An immediate, fixed emergency-stop operation that cannot set speed or position
- A bounded, validated serial protocol with no arbitrary serial-console access
- Safe starter firmware that controls only a built-in LED

Tier 7 must not create autonomous movement, polling loops, background hardware
control, vision-to-motion chains, or automatic connection to unknown ports. Real
hardware remains disabled until its exact port, telemetry fields, and actuators
are explicitly configured. Device data returned to a cloud model requires fresh
confirmation. Laptop validation does not replace hardware drivers, current
limits, physical limit switches, clear operating space, or a physical emergency
stop. Phase 7 must not add messaging, purchases, general file deletion, security
changes, or proactive behavior.

The user verified Phase 7 in a live conversation on 2026-08-16.

## Verified milestone: Phase 8

Build and independently verify:

- Scheduled local reminders with low, normal, high, and critical priorities
- High-priority deadline alerts and locally stored calendar events
- Bounded read-only battery and system-disk health warnings
- Configurable notification categories, quiet hours, thresholds, and poll rate
- A strict per-cycle notification limit
- Persistent, profile-scoped duplicate suppression across process restarts
- A visible monitor that runs only while the Argus process is open
- Explicit commands to view and confirm deletion of the delivery log

Tier 8 must be quiet by default, respect quiet hours for every category, avoid
repeating the same warning, and surface only enabled categories at or above the
configured priority threshold. It must not install an operating-system startup
task, run invisibly after Argus closes, send email or messages, sync an external
calendar, change system settings, control hardware autonomously, or claim
guaranteed delivery while the process is not running. Notification content and
delivery history remain local unless the user separately includes them in a model
conversation.

The user verified Phase 8 in a live conversation on 2026-08-16.

## Verified milestone: Phase 9

Build and independently verify:

- An explicitly started central Argus service with a lightweight text client
- Environment-backed, high-entropy bearer credentials that are never tracked
- Configured clients with profile separation and owner, user, or read-only roles
- A localhost-only default and mandatory TLS certificate/key for network binds
- Bounded JSON request bodies and per-client request-rate limits
- Remote chat, service status, delivered-notification history, and configured
  device metadata
- A provider-independent server core that can move to an always-on machine later
- Clear terminal failures without exposing API keys or bearer tokens

Tier 9 must not install a Windows service, create a startup task, listen
invisibly, accept unauthenticated private data, use unverified TLS, or expose
computer, camera, memory-write, robotics, or other action tools remotely. Device
metadata must explicitly report that remote control is disabled. A future tier
must design and verify a secure remote approval protocol before any remote action
execution is added.

The user verified Phase 9 in a live conversation on 2026-09-04.

## Verified milestone: Phase 10

Build and independently verify an **Argus Control Center**:

- A visible desktop GUI that reuses the authenticated Phase 9 client
- Conversation, delivered-notification, device-metadata, and server-status views
- A single background network worker so the interface remains responsive
- Configurable window size, refresh interval, and notification history limit
- Session-only environment-backed credentials with no token storage or display
- Strict validation of all server data before it is shown
- A hard refusal to operate if remote actions or remote device control are not
  explicitly reported as disabled
- Clean connection failures and shutdown without a hidden background process

Tier 10 must not start the server automatically, install a startup task, persist
credentials, add browser script execution, or expose computer, camera, memory-
write, robotics, or other remote actions. External content shown in the window
remains untrusted data and must never be executed as an instruction.

The user verified Phase 10 in a live conversation on 2026-09-04.

## Current architecture override: local-only Control Center

As of version 0.11, the Tier 9 remote text endpoint is retired and disabled.
The desktop Control Center runs the allowlisted agent tools locally and only in
response to a current typed or explicit voice request. Proactive polling is
disabled; closing Argus stops all workers and microphone access.

After 120 seconds without user input, the visible Control Center enters an idle
lock and stops accepting tasks. While locked, its only active input is local
offline detection of the exact return phrase "Wake up Argus, I am back". The
phrase unlocks the interface and is never executed as a task. Voice mode remains
explicitly user-enabled: selecting it replaces the chat and text composer with a
visible full voice HUD and begins a bounded listen/transcribe/respond loop. The
loop stops on Text Mode, idle lock, or exit, and never runs while Argus is closed.

## Current architecture override: explicit public web access

As of version 0.13, Argus can launch named, allowlisted HTTPS web applications,
search the live public web, and read a bounded amount of visible text from a
public page. These capabilities run only for a current typed or voice request.
Search queries and requested page URLs may be sent to public web providers.

Web content is untrusted data and never an instruction. Argus must block local
and private network targets, URL credentials, custom ports, oversized downloads,
non-text content, and unsafe redirects. Web access does not authorize scripts,
forms, logins, downloads, messages, purchases, account changes, background
browsing, or any other remote write.

## Current architecture override: installed application launching

As of version 0.14, Argus can discover and launch applications registered in the
Windows Start Menu, packaged-app catalog, and application-path registry, in
addition to configured aliases. Discovery and launching happen only for a
current user request and never in the background. Ambiguous names must be shown
to the user instead of being selected silently.

This capability grants application launching only. It does not grant arbitrary
shell commands, hidden execution, UI automation, keystroke injection, or
permission to perform sensitive actions within an opened application.
