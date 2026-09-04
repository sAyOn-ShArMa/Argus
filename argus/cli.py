"""The permanent typed interface for Argus."""

from __future__ import annotations

import atexit
import getpass
import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from argus.ai.factory import credential_environment_names, create_provider
from argus.ai.provider import ProviderError
from argus.commands import CommandResult, CommandRouter, DeviceActionRequest
from argus.config import ConfigError, load_config
from argus.core import Agent, AgentUnavailable
from argus.memory import (
    LocalMemoryStore,
    MemoryStoreError,
    build_memory_tool_definitions,
)
from argus.prompts import build_system_prompt
from argus.proactive import (
    LocalSystemReader,
    Notification,
    ProactiveEngine,
    ProactiveMonitor,
)
from argus.robotics import (
    RoboticsError,
    RoboticsService,
    build_robotics_tool_definitions,
)
from argus.tools import ToolDefinition, ToolRuntime, build_computer_tool_definitions
from argus.voice import (
    NoSpeechDetected,
    VoiceError,
    VoiceSession,
    create_voice_services,
)
from argus.wake import WakeError, WakeModeSession
from argus.vision import (
    LocalVisionService,
    VisionError,
    build_vision_tool_definitions,
)


def _session_api_key(
    provider_name: str,
    api_key_env: str = "ARGUS_API_KEY",
) -> str | None:
    for name in credential_environment_names(provider_name, api_key_env):
        existing = os.environ.get(name)
        if existing and existing.strip():
            return existing.strip()

    print(f"{api_key_env} is not set for the {provider_name} chat provider.")
    try:
        key = getpass.getpass(
            f"Paste your {provider_name} API key for this session (hidden): "
        )
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return key.strip() or None


def _console_confirmer(
    assistant_name: str,
):
    def confirm(
        definition: ToolDefinition, arguments: Mapping[str, Any]
    ) -> bool:
        print(f"\n{assistant_name}: Confirmation required for '{definition.name}'.")
        print(f"Exact arguments: {json.dumps(dict(arguments), ensure_ascii=False)}")
        if definition.name == "inspect_camera_once":
            print(
                "The camera will activate for exactly one frame; local analysis "
                "results will be returned to the model."
            )
        elif definition.name == "analyze_local_image":
            print(
                "The image stays local, but its analysis results will be returned "
                "to the model."
            )
        elif definition.name in {"get_device_status", "read_device_telemetry"}:
            print(
                "The device read is local, but its status or sensor results will "
                "be returned to the model."
            )
        elif definition.name == "actuate_device":
            print(
                "This will send one command to a physical actuator or simulator. "
                "Confirm the device, actuator, and value shown above."
            )
        try:
            answer = input("Type yes to allow this action once: ").strip().casefold()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer == "yes"

    return confirm


def _confirm_permanent_action(assistant_name: str, description: str) -> bool:
    print(f"\n{assistant_name}: Permanent deletion requires confirmation.")
    print(description)
    try:
        answer = input("Type yes to permanently delete: ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "yes"


def _confirm_device_actuation(
    assistant_name: str, request: DeviceActionRequest
) -> bool:
    print(f"\n{assistant_name}: Physical-device control requires confirmation.")
    print(
        f"Device: {request.device_id} | Actuator: {request.actuator} | "
        f"Value: {request.value}"
    )
    print("Ensure the device has clear space and a safe power source.")
    try:
        answer = input("Type yes to send this command once: ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "yes"


def _console_notifier(assistant_name: str):
    def notify(notification: Notification) -> None:
        print(f"\n{assistant_name} alert: {notification.summary()}", flush=True)

    return notify


def _handle_memory_action(
    command: CommandResult,
    *,
    store: LocalMemoryStore,
    agent: Agent,
    assistant_name: str,
) -> str:
    action = command.action
    item_id = command.payload
    if action == "clear_history":
        description = "Delete all locally stored conversation messages?"
    elif action == "clear_notifications":
        description = (
            "Delete the local notification-delivery log? Pending past events "
            "may become eligible to notify again."
        )
    else:
        labels = {
            "delete_memory": "memory",
            "delete_task": "task",
            "delete_reminder": "reminder",
            "delete_event": "calendar event",
        }
        label = labels.get(action or "", "item")
        description = f"Permanently delete {label} #{item_id}?"

    if not _confirm_permanent_action(assistant_name, description):
        return "Deletion cancelled."

    if action == "clear_history":
        deleted = store.clear_conversation()
        agent.clear_active_context()
        return f"Permanently deleted {deleted} conversation messages."
    if action == "clear_notifications":
        deleted = store.clear_notifications()
        return f"Permanently deleted {deleted} notification-delivery records."
    if item_id is None:
        return "Deletion cancelled because no item ID was provided."
    if action == "delete_memory":
        deleted = store.delete_memory(item_id)
        label = "Memory"
    elif action == "delete_task":
        deleted = store.delete_task(item_id)
        label = "Task"
    elif action == "delete_reminder":
        deleted = store.delete_reminder(item_id)
        label = "Reminder"
    elif action == "delete_event":
        deleted = store.delete_calendar_event(item_id)
        label = "Calendar event"
    else:
        return "Unknown memory action."
    if deleted:
        return f"{label} #{item_id} permanently deleted."
    return f"{label} #{item_id} was not found."


def _stream_agent_turn(agent: Agent, assistant_name: str, user_text: str) -> str | None:
    fragments: list[str] = []
    try:
        for fragment in agent.stream_turn(user_text):
            if not fragments:
                print(f"{assistant_name}: ", end="", flush=True)
            print(fragment, end="", flush=True)
            fragments.append(fragment)
    except AgentUnavailable as error:
        if fragments:
            print()
        print(f"[Model unavailable: {error}]")
        print("The failed turn was not saved; you can try again.")
        return None
    if fragments:
        print()
    return "".join(fragments).strip() or None


def _run_voice_turn(
    voice_session: VoiceSession,
    agent: Agent,
    assistant_name: str,
    max_seconds: int,
) -> None:
    print(
        f"{assistant_name}: Recording now. Speak, then press Enter to stop "
        f"(maximum {max_seconds} seconds)."
    )
    transcript = voice_session.listen()
    print(f"You (voice): {transcript}")
    reply = _stream_agent_turn(agent, assistant_name, transcript)
    if reply:
        try:
            voice_session.speak(reply)
        except VoiceError as error:
            print(f"[Speech output unavailable: {error}]")


def _run_wake_mode(
    wake_session: WakeModeSession,
    agent: Agent,
    assistant_name: str,
) -> None:
    print(
        f'{assistant_name}: Wake mode active. Listening locally for "'
        f'{wake_session.phrase.title()}". Press Ctrl+C to return to typed mode.'
    )
    while True:
        try:
            wake_session.wait()
            print(f"\n{assistant_name}: Wake word detected.")
            try:
                wake_session.acknowledge()
            except VoiceError as error:
                print(f"[Speech output unavailable: {error}]")

            transcript = None
            for attempt in range(wake_session.command_attempts):
                print(f"{assistant_name}: Listening for your command...")
                try:
                    transcript = wake_session.listen_for_command()
                    break
                except NoSpeechDetected:
                    if attempt + 1 >= wake_session.command_attempts:
                        raise
                    retry_message = "I didn't catch that. Please say the command again."
                    print(f"{assistant_name}: {retry_message}")
                    try:
                        wake_session.speak(retry_message)
                    except VoiceError as error:
                        print(f"[Speech output unavailable: {error}]")
            assert transcript is not None
            print(f"You (voice): {transcript}")
            if transcript.strip().casefold() in {
                "stop listening",
                "stop wake mode",
                "exit wake mode",
                "return to typed mode",
            }:
                print(f"{assistant_name}: Wake mode stopped.")
                return

            reply = _stream_agent_turn(agent, assistant_name, transcript)
            if reply:
                try:
                    wake_session.speak(reply)
                except VoiceError as error:
                    print(f"[Speech output unavailable: {error}]")
            print(
                f'{assistant_name}: Listening locally for "'
                f'{wake_session.phrase.title()}"...'
            )
        except VoiceError as error:
            print(f"{assistant_name}: Voice command failed: {error}")
            print(
                f'{assistant_name}: Listening locally for "'
                f'{wake_session.phrase.title()}"...'
            )
        except KeyboardInterrupt:
            print(f"\n{assistant_name}: Wake mode stopped. Typed mode restored.")
            return


def main() -> int:
    try:
        config = load_config()
        api_key = _session_api_key(
            config.ai.provider,
            config.ai.api_key_env,
        )
        provider = create_provider(
            config.ai,
            api_key=api_key,
        )
        memory_store = None
        if config.memory.enabled:
            assert config.memory.database_path is not None
            memory_store = LocalMemoryStore(
                config.memory.database_path,
                profile_id=config.memory.profile_id,
                profile_name=config.memory.profile_name,
            )
    except (ConfigError, ProviderError, MemoryStoreError) as error:
        print(f"Argus setup error: {error}", file=sys.stderr)
        return 2

    vision_service = None
    vision_setup_error = None
    if config.vision.enabled:
        try:
            vision_service = LocalVisionService(config.vision)
        except VisionError as error:
            vision_setup_error = str(error)

    robotics_service = None
    robotics_setup_error = None
    if config.robotics.enabled:
        try:
            robotics_service = RoboticsService(config.robotics)
            atexit.register(robotics_service.close)
        except RoboticsError as error:
            robotics_setup_error = str(error)

    proactive_monitor = None
    if config.proactive.enabled:
        assert memory_store is not None
        assert config.memory.database_path is not None
        proactive_engine = ProactiveEngine(
            memory_store,
            config.proactive,
            notifier=_console_notifier(config.assistant.name),
            system_reader=LocalSystemReader(config.memory.database_path.parent),
        )
        proactive_monitor = ProactiveMonitor(proactive_engine)
        atexit.register(proactive_monitor.stop)

    tool_definitions: list[ToolDefinition] = []
    if config.tools.enabled:
        tool_definitions.extend(build_computer_tool_definitions(config.tools))
    if memory_store is not None:
        tool_definitions.extend(build_memory_tool_definitions(memory_store))
    if vision_service is not None:
        tool_definitions.extend(build_vision_tool_definitions(vision_service))
    if robotics_service is not None:
        tool_definitions.extend(build_robotics_tool_definitions(robotics_service))
    tool_runtime = None
    if tool_definitions:
        tool_runtime = ToolRuntime(
            tool_definitions,
            confirmer=_console_confirmer(config.assistant.name),
            max_rounds=config.tools.max_rounds,
        )
    agent = Agent(
        provider,
        build_system_prompt(config.assistant),
        tool_runtime=tool_runtime,
        conversation_store=memory_store,
        context_limit=config.memory.conversation_context_messages,
    )
    voice_session = None
    voice_setup_error = None
    wake_session = None
    wake_setup_error = None
    if config.voice.enabled:
        try:
            speech_api_key = os.environ.get("GROQ_API_KEY")
            if not speech_api_key and config.ai.provider.casefold() == "groq":
                speech_api_key = api_key
            voice_services = create_voice_services(
                config.voice,
                config.wake,
                api_key=speech_api_key,
            )
            voice_session = voice_services.push_to_talk
            wake_session = voice_services.wake_mode
            wake_setup_error = voice_services.wake_error
        except VoiceError as error:
            voice_setup_error = str(error)
            if config.wake.enabled:
                wake_setup_error = "Voice setup failed before wake mode could start."
    commands = CommandRouter(memory_store=memory_store)

    print(f"{config.assistant.name} Phase 10")
    print(f"Provider: {provider.name} | Model: {provider.model}")
    print(f"Computer tools: {'enabled' if config.tools.enabled else 'disabled'}")
    if memory_store is not None:
        counts = memory_store.counts()
        print(
            "Local memory: enabled | "
            f"{counts['memories']} memories | "
            f"{counts['pending_tasks']} pending tasks | "
            f"{counts['pending_reminders']} pending reminders"
        )
    else:
        print("Local memory: disabled")
    if voice_session is not None:
        print(f"Voice: enabled | Output: {voice_session.output_description}")
    elif config.voice.enabled:
        print(f"Voice: unavailable | {voice_setup_error}")
    else:
        print("Voice: disabled")
    if wake_session is not None:
        print(f'Wake mode: ready | Phrase: "{config.wake.phrase.title()}"')
    elif config.wake.enabled:
        print(f"Wake mode: unavailable | {wake_setup_error}")
    else:
        print("Wake mode: disabled")
    if vision_service is not None:
        print(f"Vision: ready | {vision_service.description}")
    elif config.vision.enabled:
        print(f"Vision: unavailable | {vision_setup_error}")
    else:
        print("Vision: disabled")
    if robotics_service is not None:
        print(f"Robotics: ready | {robotics_service.description}")
    elif config.robotics.enabled:
        print(f"Robotics: unavailable | {robotics_setup_error}")
    else:
        print("Robotics: disabled")
    if proactive_monitor is not None:
        print(
            "Notification checks: manual only | use /check-alerts"
        )
    else:
        print("Proactive notifications: disabled")
    print("Remote endpoint: removed | local operation only")
    if config.dashboard.enabled:
        print(
            "Control Center: configured (not started here) | "
            "python -m argus.dashboard"
        )
    else:
        print("Control Center: disabled")
    print("Type /help for local commands.\n")
    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{config.assistant.name}: Until next time.")
            return 0

        if not user_text:
            continue

        try:
            command = commands.route(user_text, agent=agent, config=config)
        except (AgentUnavailable, MemoryStoreError, ValueError) as error:
            print(f"{config.assistant.name}: {error}")
            continue
        if command.handled:
            if command.action == "voice_turn":
                if voice_session is None:
                    print(
                        f"{config.assistant.name}: Voice is unavailable: "
                        f"{voice_setup_error or 'setup failed'}"
                    )
                    continue
                try:
                    _run_voice_turn(
                        voice_session,
                        agent,
                        config.assistant.name,
                        config.voice.max_recording_seconds,
                    )
                except VoiceError as error:
                    print(f"{config.assistant.name}: Voice input failed: {error}")
                except KeyboardInterrupt:
                    print(f"\n{config.assistant.name}: Voice turn cancelled.")
                continue
            if command.action == "wake_mode":
                if wake_session is None:
                    print(
                        f"{config.assistant.name}: Wake mode is unavailable: "
                        f"{wake_setup_error or 'setup failed'}"
                    )
                    continue
                try:
                    _run_wake_mode(wake_session, agent, config.assistant.name)
                except WakeError as error:
                    print(f"{config.assistant.name}: Wake mode failed: {error}")
                continue
            if command.action == "camera_once":
                if vision_service is None:
                    print(
                        f"{config.assistant.name}: Vision is unavailable: "
                        f"{vision_setup_error or 'setup failed'}"
                    )
                    continue
                print(
                    f"{config.assistant.name}: Camera active for one frame. "
                    "The frame will be analyzed locally and discarded."
                )
                try:
                    result = vision_service.capture_and_analyze()
                    print(f"{config.assistant.name}: {result.summary()}")
                except (VisionError, PermissionError) as error:
                    print(f"{config.assistant.name}: Vision failed: {error}")
                continue
            if command.action == "analyze_image":
                if vision_service is None:
                    print(
                        f"{config.assistant.name}: Vision is unavailable: "
                        f"{vision_setup_error or 'setup failed'}"
                    )
                    continue
                assert isinstance(command.payload, str)
                print(
                    f"{config.assistant.name}: Analyzing that image locally; "
                    "the image will not be uploaded."
                )
                try:
                    result = vision_service.analyze_image(command.payload)
                    print(f"{config.assistant.name}: {result.summary()}")
                except (VisionError, PermissionError) as error:
                    print(f"{config.assistant.name}: Vision failed: {error}")
                continue
            if command.action in {
                "device_status",
                "device_telemetry",
                "device_actuate",
                "device_estop",
            }:
                if robotics_service is None:
                    print(
                        f"{config.assistant.name}: Robotics is unavailable: "
                        f"{robotics_setup_error or 'setup failed'}"
                    )
                    continue
                assert isinstance(command.payload, DeviceActionRequest)
                request = command.payload
                try:
                    if command.action == "device_status":
                        result = robotics_service.status(request.device_id)
                    elif command.action == "device_telemetry":
                        result = robotics_service.telemetry(request.device_id)
                    elif command.action == "device_estop":
                        print(
                            f"{config.assistant.name}: Sending the fixed emergency "
                            f"stop to {request.device_id}."
                        )
                        result = robotics_service.emergency_stop(request.device_id)
                    else:
                        if not _confirm_device_actuation(
                            config.assistant.name, request
                        ):
                            print(
                                f"{config.assistant.name}: Device command cancelled."
                            )
                            continue
                        assert request.actuator is not None
                        assert request.value is not None
                        result = robotics_service.actuate(
                            request.device_id,
                            request.actuator,
                            request.value,
                        )
                    print(f"{config.assistant.name}: {result.summary()}")
                except RoboticsError as error:
                    print(f"{config.assistant.name}: Device operation failed: {error}")
                continue
            if command.action == "check_notifications":
                if proactive_monitor is None:
                    print(
                        f"{config.assistant.name}: Proactive notifications are "
                        "unavailable."
                    )
                    continue
                try:
                    delivered = proactive_monitor.check_now()
                    message = (
                        f"Delivered {len(delivered)} new notification(s)."
                        if delivered
                        else "No new eligible notifications are due."
                    )
                except MemoryStoreError as error:
                    message = f"Notification check failed: {error}"
                print(f"{config.assistant.name}: {message}")
                continue
            if command.action in {
                "delete_memory",
                "delete_task",
                "delete_reminder",
                "delete_event",
                "clear_history",
                "clear_notifications",
            }:
                assert memory_store is not None
                try:
                    message = _handle_memory_action(
                        command,
                        store=memory_store,
                        agent=agent,
                        assistant_name=config.assistant.name,
                    )
                except (AgentUnavailable, MemoryStoreError, ValueError) as error:
                    message = f"Memory operation failed: {error}"
                print(f"{config.assistant.name}: {message}")
                continue
            if command.message:
                print(f"{config.assistant.name}: {command.message}")
            if command.should_exit:
                return 0
            continue

        try:
            _stream_agent_turn(agent, config.assistant.name, user_text)
        except KeyboardInterrupt:
            print(f"\n{config.assistant.name}: Response interrupted. Until next time.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
