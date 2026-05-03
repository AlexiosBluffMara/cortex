"""Media-gate: Gemma vision describes the stimulus, returns structured JSON.

Topic-agnostic: any recorded media across any subject domain is accepted.
No content filtering is applied — the model describes what is present.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from . import config, ollama_client, prompts
from .gemma_vision import (
    _probe_duration,
    extract_audio_segment,
    extract_keyframes,
    transcribe_audio,
)


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


_AUDIO_ONLY_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a',
                    '.aac', '.wma', '.opus', '.ac3', '.aiff', '.aif'}
_IMAGE_EXTS      = {'.jpg', '.jpeg', '.png', '.webp', '.gif',
                    '.bmp', '.tiff', '.tif', '.heic', '.heif', '.avif'}


def _get_transcript(media_path: Path) -> str:
    """Extract up to 30 s of audio and transcribe with Whisper-tiny. Returns '' on failure."""
    wav = extract_audio_segment(media_path)
    if wav is None:
        return ""
    try:
        return transcribe_audio(wav)
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass


def _image_to_jpeg_b64(image_path: Path) -> str:
    """Return base64-encoded JPEG bytes. Uses PIL to convert unusual formats."""
    _NATIVE = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    if image_path.suffix.lower() in _NATIVE:
        return base64.b64encode(image_path.read_bytes()).decode()
    try:
        import io

        from PIL import Image
        with Image.open(image_path) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return base64.b64encode(image_path.read_bytes()).decode()


def classify_image(image_path: Path) -> MediaDescription:
    """Classify a still image without ffmpeg — encode directly and send to Gemma vision."""
    try:
        images_b64 = [_image_to_jpeg_b64(image_path)]
    except OSError:
        return MediaDescription(**DEFAULT.__dict__)

    user = "This is a still image (not a video clip). Classify and describe the stimulus."
    data = ollama_client.generate_json(
        prompt=user,
        system=prompts.MEDIA_GATE_SYSTEM,
        model=config.OLLAMA_MODEL_VISION,  # gemma4:31b on Big Apple by default — heavy vision describer
        images_b64=images_b64,
        num_predict=400,
        temperature=0.2,
    )
    if not isinstance(data, dict):
        return MediaDescription(**{**DEFAULT.__dict__, "frames": [image_path]})
    return MediaDescription(
        content_type=str(data.get("content_type", DEFAULT.content_type)),
        subject=str(data.get("subject", DEFAULT.subject)),
        setting=str(data.get("setting", DEFAULT.setting)),
        action=str(data.get("action", DEFAULT.action)),
        mood=str(data.get("mood", DEFAULT.mood)),
        modality=str(data.get("modality", DEFAULT.modality)),
        description=str(data.get("description", DEFAULT.description)),
        frames=[image_path],
    )


def classify(video_path: Path, n_frames: int = 4) -> MediaDescription:
    is_audio_only = video_path.suffix.lower() in _AUDIO_ONLY_EXTS
    frames = [] if is_audio_only else extract_keyframes(video_path, n=n_frames)
    if not frames and not is_audio_only:
        # No video stream found (e.g. browser-recorded .webm audio) — treat as audio-only
        is_audio_only = True

    images_b64 = [base64.b64encode(p.read_bytes()).decode() for p in frames] or None
    duration   = _probe_duration(video_path)
    transcript = _get_transcript(video_path)

    if frames:
        user = prompts.MEDIA_GATE_USER.format(n=len(frames), duration=duration)
        if transcript:
            user += f'\nAudio transcript (first 30 s): "{transcript}"'
    else:
        user = (
            f"This is a {duration:.1f}-second audio-only clip. "
            + (f'Transcript: "{transcript}"' if transcript else "No transcript available.")
            + " Classify and describe the stimulus."
        )

    data = ollama_client.generate_json(
        prompt=user,
        system=prompts.MEDIA_GATE_SYSTEM,
        model=config.OLLAMA_MODEL_VISION,  # gemma4:31b on Big Apple by default — heavy vision describer
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
