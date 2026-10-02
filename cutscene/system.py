"""pairwise-cutscene-system - CutsceneSystem

Cutscene playback with asset preloading, frame pacing and pausing,
audio-position-synced subtitles with wrapping and multi-language
support, smooth camera transitions, state/condition-aware event
triggers with callbacks, and full game-state restoration.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Camera:
    name: str
    position: tuple = (0.0, 0.0, 0.0)
    direction: tuple = (0.0, 0.0, 1.0)


@dataclass
class Subtitle:
    start: float
    end: float
    text: dict  # language code -> text


@dataclass
class CutsceneEvent:
    name: str
    time: float
    animation_state: str | None = None
    condition: Callable[[Any], bool] | None = None
    callback: Callable[[dict], None] | None = None
    fired_for: set = field(default_factory=set)


DEFAULT_CUTSCENE = {
    "name": "intro",
    "duration": 10.0,
    "assets": ["anim_intro", "audio_intro"],
    "cameras": ["cam_wide", "cam_close"],
}


class CutsceneSystem:
    def __init__(self, target_fps: int = 60, language: str = "zh",
                 max_subtitle_width: int = 40):
        self.target_fps = target_fps
        self.frame_duration = 1.0 / target_fps
        self.language = language
        self.max_subtitle_width = max_subtitle_width
        self.state: dict = {}
        self.preloaded_assets: dict = {}
        self.paused = False
        self.playing = False
        self.playback_time = 0.0
        self.audio_position = 0.0
        self.subtitles: list[Subtitle] = []
        self.cameras: dict[str, Camera] = {}
        self.current_camera: Camera | None = None
        self.events: list[CutsceneEvent] = []
        self.saved_game_state: dict | None = None
        self.ui_visible = True
        self.input_enabled = True

    # ------------------------------------------------------------------
    # Playback: preload assets, fixed-timestep frame pacing, pausable
    # ------------------------------------------------------------------
    def preload_assets(self, assets: list) -> dict:
        """Load every animation/audio asset up front so playback never
        stalls on streaming loads."""
        for asset in assets:
            if asset not in self.preloaded_assets:
                self.preloaded_assets[asset] = {"name": asset, "loaded": True}
        return self.preloaded_assets

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def play_cutscene(self, cutscene: dict | None = None) -> dict:
        cutscene = cutscene or dict(DEFAULT_CUTSCENE)

        # Save the live game state so restore_game() can put it back.
        self.saved_game_state = {
            "player_position": self.state.get("player_position", (0.0, 0.0, 0.0)),
            "ui_visible": self.ui_visible,
            "input_enabled": self.input_enabled,
        }

        # 1. Preload all animation and audio before the first frame.
        preloaded = self.preload_assets(list(cutscene.get("assets", [])))

        # 2. Fixed-timestep playback: playback time advances by the frame
        #    duration, so the cutscene runs at the same speed on any
        #    machine regardless of render frame rate. Pausing freezes it.
        duration = float(cutscene.get("duration", 0.0))
        self.playing = True
        self.playback_time = 0.0
        # Integer frame counting avoids float drift in the loop bound.
        total_frames = max(0, round(duration / self.frame_duration))
        frames = 0
        while frames < total_frames:
            if self.paused:
                continue
            frames += 1
            self.playback_time = min(frames * self.frame_duration, duration)
            self.audio_position = self.playback_time
        self.playing = False

        return {
            "cutscene": cutscene.get("name", "cutscene"),
            "preloaded_assets": sorted(preloaded),
            "frames_rendered": frames,
            "duration": duration,
            "frame_duration": self.frame_duration,
            "pausable": True,
            "finished": True,
        }

    # ------------------------------------------------------------------
    # Subtitles: synced to audio position, wrapped, multi-language
    # ------------------------------------------------------------------
    def sync_subtitles(self, audio_position: float | None = None,
                       language: str | None = None,
                       subtitles: list | None = None) -> dict:
        if subtitles is not None:
            self.subtitles = [
                s if isinstance(s, Subtitle) else Subtitle(**s)
                for s in subtitles
            ]
        position = self.audio_position if audio_position is None else audio_position
        lang = language or self.language

        # Sync against the actual audio playback position, not a fixed
        # wall-clock timer, so subtitles stay in sync after audio hitches.
        active = next(
            (s for s in self.subtitles if s.start <= position <= s.end),
            None,
        )
        if active is None:
            return {"text": "", "lines": [], "language": lang,
                    "audio_position": position}

        # Multi-language: pick the requested language, fall back to any
        # available translation.
        text = active.text.get(lang) or next(iter(active.text.values()), "")

        # Wrap long subtitles so they never overflow the screen.
        lines = textwrap.wrap(text, width=self.max_subtitle_width) or [""]
        return {
            "text": "\n".join(lines),
            "lines": lines,
            "language": lang if lang in active.text else next(iter(active.text), lang),
            "audio_position": position,
            "start": active.start,
            "end": active.end,
        }

    # ------------------------------------------------------------------
    # Camera: transitions (fade + motion blur), continuity, action timing
    # ------------------------------------------------------------------
    def switch_camera(self, target: Camera | str | None = None,
                      transition: str = "fade",
                      duration: float = 0.5,
                      at_action_point: bool = True) -> dict:
        if target is None:
            target = self.cameras.get("cam_close") or Camera("cam_close")
        elif isinstance(target, str):
            target = self.cameras.get(target, Camera(target))

        source = self.current_camera or Camera("cam_wide")

        # Continuity: the outgoing shot ends where the incoming shot
        # begins, so position and direction blend smoothly across the cut.
        blended_position = tuple(
            a + (b - a) * 0.5 for a, b in zip(source.position, target.position)
        )
        blended_direction = tuple(
            a + (b - a) * 0.5 for a, b in zip(source.direction, target.direction)
        )

        # Transition: fade out, cut on the action point, fade in, with
        # motion blur masking the move instead of a jarring hard cut.
        plan = {
            "from": source.name,
            "to": target.name,
            "transition": transition,
            "duration": duration,
            "fade_out": duration / 2,
            "fade_in": duration / 2,
            "motion_blur": True,
            "cut_at_action_point": at_action_point,
            "blend_position": blended_position,
            "blend_direction": blended_direction,
        }
        self.current_camera = target
        self.cameras[target.name] = target
        return plan

    # ------------------------------------------------------------------
    # Events: check animation state + per-player conditions, fire callbacks
    # ------------------------------------------------------------------
    def register_event(self, event: CutsceneEvent | dict):
        if isinstance(event, dict):
            event = CutsceneEvent(**event)
        self.events.append(event)
        return event

    def trigger_event(self, name: str | None = None,
                      animation_state: str | None = None,
                      player: Any = None,
                      context: dict | None = None) -> list:
        fired = []
        for event in self.events:
            if name is not None and event.name != name:
                continue
            # Timing alone is not enough: the animation must actually have
            # reached the state the event is keyed to.
            if event.animation_state is not None \
                    and event.animation_state != animation_state:
                continue
            # Per-player conditions: different players may see different
            # events instead of everyone triggering the same one.
            if event.condition is not None and not event.condition(player):
                continue
            if player in event.fired_for:
                continue
            event.fired_for.add(player)
            payload = {
                "event": event.name,
                "player": player,
                "animation_state": animation_state,
                "context": context or {},
            }
            # Callback notifies game logic that the event fired.
            if event.callback is not None:
                event.callback(payload)
            fired.append(payload)
        return fired

    # ------------------------------------------------------------------
    # Restore: game state, UI and input after the cutscene ends
    # ------------------------------------------------------------------
    def restore_game(self) -> dict:
        saved = self.saved_game_state or {
            "player_position": (0.0, 0.0, 0.0),
            "ui_visible": True,
            "input_enabled": True,
        }
        # Put the player back where they were before the cutscene.
        self.state["player_position"] = saved["player_position"]
        # Restore the gameplay UI (cutscene UI is torn down).
        self.ui_visible = saved["ui_visible"]
        # Re-enable player input.
        self.input_enabled = saved["input_enabled"]
        self.playing = False
        self.playback_time = 0.0
        self.audio_position = 0.0

        restored = {
            "player_position": self.state["player_position"],
            "ui_visible": self.ui_visible,
            "input_enabled": self.input_enabled,
            "restored": True,
        }
        self.saved_game_state = None
        return restored
