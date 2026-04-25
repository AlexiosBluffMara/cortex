"""Tests for cortex.prompts — system/user prompt templates."""
from __future__ import annotations

import pytest

from cortex.prompts import MEDIA_GATE_SYSTEM, MEDIA_GATE_USER, PERSONA

pytestmark = pytest.mark.unit


class TestPersona:
    def test_persona_identifies_as_cortex(self):
        # Branding: must say "Cortex", never "MindScope" or "Jemma".
        assert "Cortex" in PERSONA
        assert "MindScope" not in PERSONA
        assert "Jemma" not in PERSONA

    def test_persona_states_offline_constraint(self):
        # The local-first identity is part of the Gemma 4 Good pitch.
        assert "offline" in PERSONA.lower() or "local" in PERSONA.lower()

    def test_persona_lists_no_diagnosis_rule(self):
        # Hard rule — never offer medical diagnosis.
        assert "diagnosis" in PERSONA.lower() or "medical" in PERSONA.lower()


class TestMediaGate:
    def test_media_gate_system_includes_persona(self):
        # System prompt embeds the persona prefix.
        assert PERSONA in MEDIA_GATE_SYSTEM

    def test_media_gate_system_demands_json_only(self):
        # The "JSON object" + "no prose" requirement is what keeps the parser sane.
        assert "JSON" in MEDIA_GATE_SYSTEM
        assert "ONLY" in MEDIA_GATE_SYSTEM or "only" in MEDIA_GATE_SYSTEM

    def test_media_gate_user_template_renders(self):
        rendered = MEDIA_GATE_USER.format(n=12, duration=20.0)
        assert "12 keyframes" in rendered
        assert "20.0" in rendered

    def test_media_gate_required_fields(self):
        # The content-classification schema must include these fields verbatim,
        # because the parser keys off them.
        required = ("content_type", "subject", "setting", "action", "mood", "modality", "description")
        for field in required:
            assert field in MEDIA_GATE_SYSTEM
