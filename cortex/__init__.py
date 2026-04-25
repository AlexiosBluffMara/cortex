"""Cortex core — interface-agnostic TRIBE v2 + Gemma inference engine.

Extracted from D:\\TRIBEV2\\bot\\ for use by CLI, web, Discord, and cloud interfaces.

Modules:
  config          — paths, env vars, model tier config
  pipeline        — TRIBE v2 inference with Blackwell optimizations
  model_manager   — Gemma 4 model lifecycle (swap, warm, unload)
  gpu_scheduler   — exclusive VRAM arbitration between TRIBE and Gemma
  request_queue   — priority queue with fallback routing
  media_processor — FFmpeg preprocessing for TRIBE and Gemma vision
  ollama_client   — tier-aware Ollama API calls
  tiers           — 7-tier narration system
  prompts         — all system/user prompt templates
  analysis        — multi-atlas brain parcellation
  visualize       — matplotlib heatmap renders
  gemma_vision    — Gemma 4 multimodal vision gate
"""
