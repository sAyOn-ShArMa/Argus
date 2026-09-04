# Argus

Argus is a free-first, modular personal AI assistant. The current 0.14 release
uses a standalone local Control Center with an original dark red cinematic HUD,
on-demand tools, explicit voice turns, and a two-minute idle lock.

The full direction and non-negotiable approval rules are in
[AGENT.md](AGENT.md).

## Current capabilities

- Streaming typed conversation with local context that survives restarts
- Profile-scoped SQLite storage for conversation, memories, tasks, and reminders
- Explicit commands to inspect stored information and confirm permanent deletion
- Explicit one-frame webcam capture with a visible terminal indicator
- Local object, QR-code, anonymous-face, and canned hand-gesture recognition
- Allowlisted local image analysis without uploading the raw image
- Explicit `/voice` push-to-talk turns
- Visible `/wake` mode with local offline wake-phrase detection
- Hands-free command capture that stops after speech followed by silence
- Groq Whisper command transcription using the existing API credential
- Local Windows text-to-speech with preferred male-voice selection
- Provider-independent AI, speech, wake, and tool interfaces
- Provider-independent simulated and serial robotics/IoT device interfaces
- Allowlisted sensor telemetry, confirmed actuator writes, and emergency stop
- Locally stored reminders, deadlines, calendar events, and alert history
- Manual, user-requested notification checks with no automatic polling
- Allowlisted applications, websites, file search, system information, media
  keys, and confirmed shell-free command execution
- Named web-app launching plus live public web search and bounded page reading
- On-demand launching of applications registered in the Windows Start Menu or
  application registry, without granting arbitrary shell access
- Human-readable, strictly validated JSON configuration
- Standalone local Control Center for commands, chat, voice, alerts, and devices
- Trusted success receipts keep later replies consistent with completed actions
- Automatic visible voice loop; the chat and composer are removed in voice mode
- Two-minute idle lock that accepts only “Wake up Argus, I am back” locally
- No remote chat endpoint and no server token requirement

Argus still has no automatic startup service or camera access; face identity
recognition, stored camera frames,
application closing, arbitrary terminal access, messaging, purchasing, security
changes, autonomous robotics, external calendar synchronization, background
alerts while Argus is closed, Wi-Fi/MQTT device transport, remote action
execution, or general file deletion.

## Technology

- Python 3.11+
- Groq and `openai/gpt-oss-120b` for chat
- Groq `whisper-large-v3-turbo` for command transcription
- `sounddevice` for in-memory microphone capture
- `pyttsx3` and Windows SAPI for local speech output
- Vosk plus `vosk-model-small-en-us-0.15` for offline wake detection
- SQLite from the Python standard library for local durable memory
- OpenCV for camera access and QR-code decoding
- MediaPipe 0.10.21 with local Google model files for objects, faces, and gestures
- `pyserial` 3.5 for bounded Arduino/ESP32 USB serial communication
- Python threads, SQLite, `ctypes`, and `shutil` for local on-demand operations
- Python's bundled Tk 8.6 toolkit for the desktop Control Center
- Python standard-library computer controls

Provider, model, tool allowlists, voice, wake, memory, vision, robotics, and
dashboard settings live in
[config/argus.json](config/argus.json).
API keys never belong in that file.

## Internet and web applications

Argus can now open approved web applications by name in the default browser.
The default catalog includes Google, YouTube, Gmail, Google Drive, Google
Calendar, GitHub, ChatGPT, and Spotify. Edit `tools.web_applications` in
`config/argus.json` to add or remove HTTPS applications.

The catalog is only a shortcut list, not a website allowlist. Argus may open any
public HTTP or HTTPS website you explicitly request. If you provide only an
unknown site name, it searches for the public URL first instead of guessing.

Examples for text or voice mode:

- `Open YouTube`
- `Launch Gmail`
- `Search the web for today's robotics news`
- `Find the latest Python release and summarize the sources`

Live search tries DuckDuckGo and then Bing without a separate API key. Search
queries are sent to the selected public search provider. Argus can read a
bounded amount of visible text from public pages, but it cannot execute page
scripts, submit forms, sign in, download files, send messages, or make
purchases. Localhost, private-network addresses, credentials in URLs, custom
ports, oversized responses, and non-text content are blocked.

## Installed Windows applications

Argus can launch applications registered with Windows, not just the four aliases
in `config/argus.json`. When you say `Open Arduino IDE`, `Launch Excel`, or
`Start OBS Studio`, Argus discovers the matching Start Menu, packaged-app, or
registered executable entry locally and launches that exact match. If a name is
ambiguous, it returns the matching names instead of choosing silently.

Discovery runs only for the current request and is cached locally for one minute;
there is no background scanning. This grants launch access, not unrestricted UI
control, keystroke injection, terminal access, or permission to perform sensitive
actions inside an application. Portable executables that are not registered with
Windows still need a configured alias.

## Set up

Create a Groq API key at <https://console.groq.com/keys>.

From PowerShell in this directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe .\scripts\setup_wake_model.py
.\.venv\Scripts\python.exe .\scripts\setup_vision_models.py
```

The setup script downloads the official Apache-licensed Vosk small English
model, verifies its pinned SHA-256 digest, checks archive paths before
extraction, and stores it under the ignored `models` directory. The model is
about 40 MB to download and about 71 MB extracted.

The vision setup script downloads three official MediaPipe model files, verifies
their pinned SHA-256 hashes, and stores about 13 MB under the ignored `models`
directory. MediaPipe is intentionally pinned to 0.10.21: newer prebuilt releases
send API performance/utilization metrics, while Argus's Tier 6 privacy boundary
requires no vision-library network activity.

## Run

```powershell
.\.venv\Scripts\python.exe -m argus
```

If `GROQ_API_KEY` is not set, Argus asks for it with hidden input. To avoid
entering it on every launch, add `GROQ_API_KEY` under Windows **User environment
variables**, then restart VS Code. Never put the key in `config/argus.json`.

## Local Control Center

The Tier 9 remote text endpoint has been retired. The Control Center connects
directly to the local, allowlisted Argus tool runtime and requires only the
existing `GROQ_API_KEY`:

```powershell
.\.venv\Scripts\python.exe -m argus.dashboard --check
.\.venv\Scripts\python.exe -m argus.dashboard
```

The window provides local command/chat, alert history, configured devices, and
core status. Use **TEXT MODE** or **VOICE MODE**; voice mode removes both the chat
and text composer and replaces them with a full listening HUD. Selecting Voice
Mode starts a visible listen → transcribe → act → speak loop automatically and
continues until Text Mode, idle lock, or exit. Tool work runs on one worker so
the GUI stays responsive, and every sensitive action opens a fresh confirmation
panel showing its exact arguments.

After 120 seconds without user activity, command processing locks and the
visible GUI listens locally only for “Wake up Argus, I am back.” The phrase
restores the interface; it is not treated as a command. Closing Argus ends the
worker and microphone listener. `disconnect`, `/disconnect`, and `/sleep` lock
immediately; `/exit` and `/quit` close Argus.

## Wake mode

Type `/wake` to visibly activate wake listening. The terminal shows that the
microphone is active. Vosk listens locally for `Argus`; common recognition
variants `August` and `Argos` are accepted as aliases. Wake audio stays on the
laptop and is never sent to an API.

After activation, Argus says "Yes, sir?" and records the follow-up command. Vosk
detects the end of the spoken phrase locally, with a 1.5-second level-based
silence fallback and a 15-second hard limit. If no command is heard, Argus asks
once for the command again without making you repeat the wake phrase. Only a
captured follow-up command is sent to Groq for transcription. The in-memory WAV
is then discarded; no recording is saved.

Return to typed mode with **Ctrl+C**, or activate Argus and say one of:

- `Stop listening`
- `Stop wake mode`
- `Exit wake mode`
- `Return to typed mode`

Wake mode never starts automatically. Normal typed mode and `/voice` do not
listen in the background.

If `Argus` is not recognized reliably, speak it clearly as two syllables. If
the word `August` causes false activations, remove `august` from
`wake.recognition_aliases` in the config.

## Safety model

All model-selected tools pass through the same local runtime. It validates tool
names and arguments, limits files and applications to allowlists, restricts web
URLs and media actions, and runs commands as an argument list with `shell=False`.
Every command run requires a fresh confirmation; only the full word `yes`
approves that one action.

Search results and downloaded page text are untrusted external data. They may be
used as information, but never as instructions or as permission to run another
tool. Internet access occurs only for a current user request while Argus is
open; it does not browse or poll in the background.

Wake mode does not change any tool approval rule. A voice command that proposes
a confirmed action still pauses and asks for typed confirmation.

Model-selected writes to explicit memory, tasks, or reminders also require a
fresh typed `yes`. The model has no permanent-delete tool. Deletion is available
only through explicit slash commands and requires another full typed `yes`.

Model-selected image or camera analysis always requires a fresh typed `yes`.
The image itself remains local, but the resulting labels, QR text, face count,
and gesture names are returned to the language model after approval. Direct
`/camera` and `/vision` commands print the local result without involving the
language model.

Model-selected device status and telemetry reads require a fresh typed `yes`
before local device data is returned to Groq. Every actuator write also requires
a fresh typed `yes`, even for the simulator. The fixed emergency-stop operation
cannot set speed or position and is intentionally immediate.

## Local vision and camera privacy

`/camera` activates the configured webcam only long enough to warm it up and
capture one frame. The terminal displays an active-camera message first. Argus
analyzes that frame in memory and releases the camera in a `finally` boundary;
the frame is not saved. There is no live/background camera mode.

`/vision <path>` analyzes a BMP, JPEG, PNG, or WebP file of at most 25 MB. The
file must be inside a folder listed in `vision.allowed_image_roots`; the default
allows only this Argus project. Processing is local and reports supported COCO
objects, readable QR content, anonymous face boxes/count, and the supported
MediaPipe gestures `Closed_Fist`, `Open_Palm`, `Pointing_Up`, `Thumb_Down`,
`Thumb_Up`, `Victory`, and `ILoveYou`.

Face detection only locates face-shaped regions. Argus does not name, identify,
enroll, compare, or retain anyone's face.

## Robotics and IoT safety

Tier 7 starts with `sim_robot`, so it works without touching physical hardware.
It reports deterministic distance, environmental, battery, and obstacle sensor
values and simulates LED, servo, and two bounded motor channels.

Real Arduino and ESP32 devices use the local `ARGUS/1` newline protocol through
one exact allowlisted serial port. Serial connections are opened only when a
device command is requested. Frames are capped at 4096 bytes, telemetry channel
names are allowlisted, actuator values are bounded, malformed responses are
rejected, and there is no raw serial-console tool.

Argus performs no polling, autonomous navigation, vision-to-motion chain, or
background device control. Laptop confirmation is only one safety layer: real
motors still require suitable drivers, power protection, physical limits, clear
space, and a hardware emergency stop. The safe built-in-LED starter firmware and
connection guide are in [firmware/README.md](firmware/README.md).

## Local memory and privacy

Completed user/assistant turns are saved by default in the local, non-OneDrive
file `%LOCALAPPDATA%\Argus\argus.db`. Argus loads at most the last 20 messages
into active context at startup. `/reset` starts a fresh context but retains the
stored history; `/clear-history` is the confirmed permanent deletion command.

Explicit memories are separate from conversation history. `/remember` is an
explicit local write, `/memories` lets you inspect the records, and `/forget`
requests confirmed deletion. The schema is already profile-scoped so authorized
users can be separated later, although only the `owner` profile is configured.

The database stays on the laptop. When Argus answers through Groq, active
conversation context and any memory returned by a model-requested search are sent
to Groq as part of that request.

## On-demand notifications

Automatic notification polling is disabled in the current configuration.
Stored reminders, calendar items, and earlier delivery history remain locally
available, but Argus does not scan or deliver them on its own. It does not
install a Windows startup task, run when Argus is closed, send email/messages,
or synchronize an external calendar.

`/notifications` shows the local delivery log. `/clear-notifications` requests
confirmed permanent deletion; clearing it can make still-pending past events
eligible again. Reminder, deadline, calendar, and system categories and thresholds
can be changed in the `proactive` configuration section.

## Voice and microphone troubleshooting

If Windows blocks the microphone, open **Settings > Privacy & security >
Microphone** and enable microphone access for desktop applications.

The selected local voice, rate, volume, recording limits, retry count, silence
duration, and fallback speech threshold can be changed in the JSON config. Vosk
endpoint detection is primary; `wake.speech_threshold` is only the fallback for
speech the small local model cannot decode cleanly.

Groq documents a 10-second minimum usage length for each transcription request.
Your account's current plan quota and rate limits apply. See the
[Groq speech-to-text documentation](https://console.groq.com/docs/speech-to-text).

## Built-in commands

- `/help` - list local commands
- `/status` - show provider, model, context, voice, and wake status
- `/dashboard-status` - show the local Control Center configuration
- `/tools` - list model-selected tools and confirmation status
- `/voice` - perform one explicit push-to-talk turn
- `/wake` - enter visible local wake mode
- `/camera` - explicitly capture, locally analyze, and discard one webcam frame
- `/vision <path>` - locally analyze one approved image file
- `/vision-status` - show the active camera/privacy boundary
- `/robotics-status` and `/devices` - show the Tier 7 safety state and devices
- `/device-status <id>` - locally read one configured device's state
- `/telemetry <id>` - locally read one allowlisted sensor snapshot
- `/actuate <id> <actuator> <value>` - request one confirmed bounded write
- `/estop <id>` - immediately send the fixed emergency-stop operation
- `/remember <text>` and `/memories` - store and inspect explicit memories
- `/forget <id>` - request confirmed permanent deletion of one memory
- `/task add <text>`, `/tasks`, and `/task done <id>` - manage tasks
- `/task delete <id>` - request confirmed permanent task deletion
- `/remind YYYY-MM-DD HH:MM | [priority |] text` - schedule a reminder
- `/deadline YYYY-MM-DD HH:MM | [priority |] text` - schedule a deadline alert
- `/reminder done <id>` and `/reminder delete <id>` - manage reminders
- `/events` and `/event add YYYY-MM-DD HH:MM | [priority |] title` - local calendar
- `/event done <id>` and `/event delete <id>` - manage local events
- `/notifications-status` - show whether manual notification checks are enabled
- `/check-alerts` - immediately perform one safe local due-event check
- `/notifications [limit]` - show delivered alerts
- `/clear-notifications` - request confirmed deletion of the delivery log
- `/history [1-100]` - inspect stored conversation messages
- `/clear-history` - request confirmed permanent conversation deletion
- `/time` and `/date` - report local time or date without an API call
- `/reset` - start fresh context without deleting stored history
- `/exit` or `/quit` - stop Argus

## Test

The automated suite mocks microphone capture, transcription, speech output,
wake detection, applications, browsers, media controls, subprocesses, camera
capture, vision tools, simulated devices, bounded serial frames, sensor
allowlists, actuator limits, emergency stop, notification priorities, quiet
hours, calendar lead time, system thresholds, duplicate suppression, and
confirmation denial. It also covers local dashboard commands, exact idle-wake
phrase handling, bounded chat, dynamic voice mode, and local device display.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Phase 6 live verification (completed)

1. Start Argus and confirm it reports `Vision: ready`.
2. Type `/vision-status` and confirm it says one-frame local analysis, no saved
   frames, and no face identity recognition.
3. Hold up an ordinary object such as a cup, type `/camera`, and confirm the camera
   indicator appears, the camera light turns off again, and an object result is
   printed. Detection is probabilistic, so try good lighting and a clear view.
4. Hold a `Thumb_Up` gesture in view before running `/camera`; confirm the gesture
   is reported. Try again if the one captured frame catches motion blur.
5. Display a QR code containing `ARGUS-TIER-6`, run `/camera`, and confirm that text
   is decoded locally.
6. Put a test JPG or PNG inside the Argus project, then run `/vision "full path"`;
   confirm the analysis appears without an API confirmation prompt.
7. Ask conversationally `Argus, what can you see through the camera?` Type anything
   except full `yes` and confirm no capture occurs. Ask again, type `yes`, and
   confirm only one-frame analysis runs.

The user completed this live verification on 2026-08-16.

## Phase 7 live verification (completed)

The default verification uses only the simulator and cannot move real hardware.

1. Restart Argus and confirm it reports `Robotics: ready`.
2. Run `/robotics-status` and confirm it says there is no background or
   autonomous control.
3. Run `/devices`, then `/device-status sim_robot`.
4. Run `/telemetry sim_robot`; it should report `distance_cm=240.0 cm` plus
   temperature, humidity, battery, and obstacle values.
5. Run `/actuate sim_robot servo 90`. Type anything except the full word `yes`
   and confirm the command is cancelled.
6. Repeat the command, type `yes`, and confirm the simulator state becomes
   `positioned`.
7. Run `/actuate sim_robot motor_left 25`, approve it, then run
   `/device-status sim_robot` and confirm the state is `moving`.
8. Run `/estop sim_robot`, then check status and confirm it is
   `emergency_stopped`.
9. Ask conversationally, "Where is the robot?" Approve the telemetry read and
   confirm Argus reports the simulated 2.4 metre distance instead of inventing a
   location.

The user completed this simulator verification on 2026-08-16. Connecting real
hardware remains a separate opt-in test using [firmware/README.md](firmware/README.md).

## Phase 8 live verification (completed)

1. Restart Argus and confirm it reports `Argus Phase 8` and
   `Proactive notifications: ready`.
2. Run `/notifications-status`. Confirm the poll interval, quiet hours,
   categories, minimum priority, and whether notifications are allowed now.
3. If it says `quiet now`, perform the timed portion after 07:00 or temporarily
   set both quiet-hour values to `00:00` and restart Argus.
4. Schedule a reminder one or two minutes ahead using your current local date and
   time, for example:

   ```text
   /remind 2026-08-16 14:30 | high | Tier 8 reminder test
   ```

5. Leave Argus open. Within 30 seconds after the scheduled minute, confirm a
   visible `Argus alert` appears.
6. Run `/notifications`, then `/check-alerts`. Confirm the reminder is recorded
   once and is not repeated.
7. Add a calendar event within the next 15 minutes:

   ```text
   /event add 2026-08-16 14:40 | normal | Tier 8 calendar test
   ```

   Run `/check-alerts` and confirm the calendar lead-time alert appears once.
8. Run `/deadline` with a due time and confirm it is stored with high priority
   by default.
9. Close Argus and confirm no background process or Windows notification service
   remains. Tier 8 alerts intentionally require Argus to be open.

The user completed the live reminder and duplicate-suppression verification on
2026-08-16.

## Retired remote milestone

Tier 9's server and remote text client were previously verified, then retired by
design. They are disabled in configuration and no longer installed as command
entry points. The current Control Center is local-only.

## Local Control Center live verification

1. Run `python -m argus.dashboard --check`; confirm it reports local tools,
   disabled proactive automation, and no remote endpoint.
2. Run `python -m argus.dashboard`; confirm the dark red HUD opens without a
   server or `ARGUS_SERVER_TOKEN`.
3. Ask “Open Notepad.” Confirm Notepad opens through the approved local app tool.
4. Ask to run `hostname.exe`. Confirm the red one-action approval panel appears
   before anything runs; deny once, then try again and allow once.
5. Select **VOICE MODE**. Confirm the chat and text composer disappear, the
   voice HUD fills the console, and listening starts without another button.
   Speak one command, pause, and confirm listening automatically resumes.
6. Leave the GUI untouched for two minutes. Confirm the command interface locks
   while the window stays visible.
7. Say “Wake up Argus, I am back.” Confirm the local interface returns without
   treating that phrase as a task.
8. Close Argus and confirm no Argus worker or microphone listener remains.
