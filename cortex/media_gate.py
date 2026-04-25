"""Media-gate: Gemma vision describes the stimulus, returns structured JSON.

Topic-agnostic: any recorded media across any subject domain is accepted.
No content filtering is applied — the model describes what is present.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from . import config, ollama_client, prompts
from .gemma_vision import _probe_duration, extract_keyframes


@dataclass
class MediaDescription:
    content_type: str
    subject: str
    setting: str
    action: str
    mood: str
    modality: str
    description: str
    frames: list[Path] = field(default_factory=list)

    def short_description(self) -> str:
        """Single-paragraph description — what Gemma 'sees' in the clip."""
        if self.description:
            return self.description.strip()
        parts = [self.subject]
        if self.setting:
            parts.append(f"in {self.setting}")
        if self.action:
            parts.append(self.action)
        if self.mood and self.mood != "neutral":
            parts.append(f"({self.mood})")
        return ", ".join(p for p in parts if p) + "."

    def summary_line(self) -> str:
        """One-line headline suitable for embed titles or log entries."""
        return f"{self.content_type} of {self.subject} ({self.modality})"


DEFAULT = MediaDescription(
    content_type="unknown",
    subject="unidentified subject",
    setting="unknown setting",
    action="unknown action",
    mood="neutral",
    modality="unknown",
    description="Unable to parse the media content from the keyframes.",
    frames=[],
)


def classify(video_path: Path, n_frames: int = 4) -> MediaDescription:
    frames = extract_keyframes(video_path, n=n_frames)
    if not frames:
        print(f"[media_gate] ffmpeg produced no frames for {video_path.name}; using text-only fallback")
        return MediaDescription(**{**DEFAULT.__dict__, "frames": []})
    images_b64 = [base64.b64encode(p.read_bytes()).decode() for p in frames]
    duration = _probe_duration(video_path)
    user = prompts.MEDIA_GATE_USER.format(n=len(frames), duration=duration)
    data = ollama_client.generate_json(
        prompt=user,
        system=prompts.MEDIA_GATE_SYSTEM,
        model=config.OLLAMA_MODEL_FAST,
        images_b64=images_b64,
        num_predict=400,
        temperature=0.2,
    )
    if not isinstance(data, dict):
        return MediaDescription(**{**DEFAULT.__dict__, "frames": frames})
    return MediaDescription(
        content_type=str(data.get("content_type", DEFAULT.content_type)),
        subject=str(data.get("subject", DEFAULT.subject)),
        setting=str(data.get("setting", DEFAULT.setting)),
        action=str(data.get("action", DEFAULT.action)),
        mood=str(data.get("mood", DEFAULT.mood)),
        modality=str(data.get("modality", DEFAULT.modality)),
        description=str(data.get("description", DEFAULT.description)),
        frames=frames,
    )
