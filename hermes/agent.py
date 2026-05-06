"""
Cortex Agent — Hermes Agent configuration and tool registry.

This is the main entry point for the Hermes Agent fork. It registers
all Cortex tools and configures the agent to use Gemma 4 via Ollama.

Usage:
    from hermes.agent import CortexAgent
    agent = CortexAgent()
    await agent.run("Analyze this video: /path/to/clip.mp4")

The agent orchestrates:
  - brain_scan:      Full TRIBE v2 inference pipeline
  - narrate:         Multi-tier narration of brain results
  - visualize:       Heatmaps, bar charts, 3D viewer
  - describe_input:  Gemma 4 vision-based content classification
"""
from __future__ import annotations

import asyncio
from typing import Any

from cortex import config
from cortex.gpu_scheduler import get_scheduler
from cortex.logger import log

# Tool registry — maps tool names to their modules
_TOOL_REGISTRY: dict[str, Any] = {}


def _load_tools() -> None:
    """Lazy-load all tool modules and register them."""
    if _TOOL_REGISTRY:
        return

    from hermes.tools import brain_scan, describe_input, narrate, visualize

    _TOOL_REGISTRY["brain_scan"] = {
        "schema": brain_scan.TOOL_SCHEMA,
        "execute": brain_scan.execute,
    }
    _TOOL_REGISTRY["narrate"] = {
        "schema": narrate.TOOL_SCHEMA,
        "execute": narrate.execute,
    }
    _TOOL_REGISTRY["visualize"] = {
        "schema": visualize.TOOL_SCHEMA,
        "execute": visualize.execute,
    }
    _TOOL_REGISTRY["describe_input"] = {
        "schema": describe_input.TOOL_SCHEMA,
        "execute": describe_input.execute,
    }


def get_tool_schemas() -> list[dict]:
    """Return all tool schemas for Hermes tool registration."""
    _load_tools()
    return [t["schema"] for t in _TOOL_REGISTRY.values()]


async def execute_tool(name: str, **kwargs) -> dict:
    """Execute a registered tool by name."""
    _load_tools()
    if name not in _TOOL_REGISTRY:
        return {"ok": False, "error": "unknown_tool", "message": f"No tool named '{name}'"}

    tool = _TOOL_REGISTRY[name]
    try:
        return await tool["execute"](**kwargs)
    except Exception as exc:
        log.error("[agent] Tool '%s' failed: %s", name, exc)
        return {"ok": False, "error": "tool_error", "message": str(exc)}


class CortexAgent:
    """
    Cortex Agent wrapping Hermes with TRIBE v2 brain analysis tools.

    The agent uses Gemma 4 via Ollama for reasoning and tool calling,
    with TRIBE v2 for brain response prediction. GPU access is managed
    by the GPUScheduler — TRIBE and Gemma never coexist in VRAM.
    """

    SYSTEM_PROMPT = (
        "You are Cortex, an AI neuroscience research assistant built by "
        "Red Team Kitchen. You analyze video and audio stimuli to predict "
        "how the human brain would respond, using Meta's TRIBE v2 brain "
        "foundation model.\n\n"
        "You have access to the following tools:\n"
        "- brain_scan: Analyze media through TRIBE v2 (takes 4-7 minutes)\n"
        "- narrate: Generate explanations of brain responses at any expertise level\n"
        "- visualize: Create brain activation visualizations\n"
        "- describe_input: Classify video content using Gemma 4 vision\n\n"
        "When a user sends a video or audio file:\n"
        "1. Use describe_input to understand the content\n"
        "2. Use brain_scan to predict cortical responses\n"
        "3. Use narrate to explain the results at the appropriate level\n"
        "4. Use visualize to create charts if requested\n\n"
        "Always explain that TRIBE v2 predictions are group-averaged estimates "
        "based on population-level fMRI data, not individual brain scans. "
        "Never offer medical diagnoses.\n\n"
        "You run locally on an RTX 5090 GPU. Be honest about processing times "
        "(brain scans take 4-7 minutes) and VRAM constraints."
    )

    def __init__(self, ollama_url: str | None = None):
        self._ollama_url = ollama_url or config.OLLAMA_URL
        self._scheduler = get_scheduler()
        _load_tools()
        self._history: list[dict] = []

    @property
    def tools(self) -> list[dict]:
        return get_tool_schemas()

    async def startup(self) -> None:
        """Initialize the agent — warm Gemma E4B."""
        log.info("[agent] Starting Cortex Agent...")
        await self._scheduler.ensure_gemma()
        log.info("[agent] Cortex Agent ready. GPU state: %s", self._scheduler.state.value)

    async def run_tool(self, tool_name: str, **kwargs) -> dict:
        """Execute a tool and return the result."""
        log.info("[agent] Running tool: %s", tool_name)
        result = await execute_tool(tool_name, **kwargs)
        log.info("[agent] Tool '%s' completed. ok=%s", tool_name, result.get("ok"))
        return result

    async def chat(self, user_message: str) -> str:
        """
        Process a user message through Gemma for reasoning/tool selection.

        This is the main conversational interface. Gemma decides which
        tools to call based on the user's message.
        """
        from cortex.ollama_client import generate

        self._history.append({"role": "user", "content": user_message})

        # Build context with tool descriptions
        tools_desc = "\n".join(
            f"- {t['schema']['name']}: {t['schema']['description']}"
            for t in _TOOL_REGISTRY.values()
        )

        prompt = (
            f"Available tools:\n{tools_desc}\n\n"
            f"User message: {user_message}\n\n"
            f"If the user wants to analyze media, respond with the tool to use "
            f"and its parameters. Otherwise, respond conversationally."
        )

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: generate(
                prompt=prompt,
                system=self.SYSTEM_PROMPT,
                model=config.OLLAMA_MODEL_FAST,
                temperature=0.4,
                num_predict=500,
            ),
        )

        self._history.append({"role": "assistant", "content": response})
        return response

    def gpu_status(self) -> dict:
        """Return current GPU and queue status."""
        return self._scheduler.vram_report()
