"""
Claude Code CLI wrapper for agent calls — covered by Claude Pro, no API credits needed.

Each agent call runs: echo <user_message> | claude -p --system-prompt <prompt> [--allowed-tools ...]

Agents that need live external data get WebFetch + WebSearch tools.
Agents that only process packet data get no external tools (faster, no side-effects).

Web-enabled agents (fetch news, SEC filings, macro data):
    news_reporter, macro_intelligence, fundamental_thesis

Data-only agents (work entirely from blind packet):
    technical_screener, execution
"""

from __future__ import annotations
import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger("pipeline.agent_caller")

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

PROMPT_FILES = {
    "technical_screener": "technical_screener_agent.md",
    "macro_intelligence": "macro_intelligence_agent.md",
    "news_reporter":      "news_reporter_agent.md",
    "fundamental_thesis": "fundamental_thesis_agent.md",
    "execution":          "execution_agent.md",
}

# Tools available to web-enabled agents
WEB_TOOLS    = "WebFetch,WebSearch"
# Data-only agents get no external tools
NO_EXT_TOOLS = ""

AGENTS_WITH_WEB: set[str] = {
    "news_reporter",
    "macro_intelligence",
    "fundamental_thesis",
}

MODEL          = "claude-sonnet-4-6"
TIMEOUT_S      = 600   # 10 minutes per agent (web agents may take longer)
MAX_RETRIES    = 2
RETRY_DELAY_S  = 15

# On Windows, subprocess doesn't inherit the shell PATH — use full path to claude.cmd
import shutil as _shutil
_claude_cmd = _shutil.which("claude") or r"C:\Users\kanna\AppData\Roaming\npm\claude.cmd"
CLAUDE_CMD: str = _claude_cmd


def _load_prompt(agent: str) -> str:
    fname = PROMPT_FILES.get(agent)
    if not fname:
        raise ValueError(f"Unknown agent: {agent!r}")
    return (PROMPTS_DIR / fname).read_text(encoding="utf-8")


def _run_cli(system_prompt: str, user_message: str, tools: str) -> str:
    """
    Run claude CLI as subprocess, return stdout text.

    stdin  = user_message (the blind packet JSON + instruction)
    stdout = agent response (human-readable + JSON block)
    """
    cmd = [
        CLAUDE_CMD, "--print",
        "--model",         MODEL,
        "--system-prompt", system_prompt,
        "--no-session-persistence",
        "--output-format", "text",
    ]

    if tools:
        cmd += ["--allowed-tools", tools]
    else:
        # Explicitly disable all tools for data-only agents
        cmd += ["--tools", ""]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd,
                input=user_message,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=TIMEOUT_S,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            # Non-zero exit — log stderr and retry
            log.warning(
                f"CLI exit {result.returncode} (attempt {attempt}): "
                f"{result.stderr[:300]}"
            )
        except subprocess.TimeoutExpired:
            log.warning(f"CLI timeout after {TIMEOUT_S}s (attempt {attempt})")
        except Exception as e:
            log.warning(f"CLI error (attempt {attempt}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_S * attempt)

    raise RuntimeError(
        f"Claude CLI failed after {MAX_RETRIES} attempts. "
        f"Last stderr: {result.stderr[:500] if 'result' in dir() else 'N/A'}"
    )


def call_agent(agent: str, packet: dict) -> str:
    """
    Call a single agent with its blind packet via Claude Code CLI.

    Args:
        agent:  one of the PROMPT_FILES keys
        packet: blind packet dict (pre-filtered by data/packets.py)

    Returns:
        Raw agent response text (human-readable + JSON block).
    """
    system_prompt = _load_prompt(agent)
    user_message = (
        "Here is your blind packet for this run:\n\n"
        "```json\n"
        + json.dumps(packet, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Process the packet and produce your full output including the "
        "machine-readable JSON block at the end."
    )

    tools = WEB_TOOLS if agent in AGENTS_WITH_WEB else NO_EXT_TOOLS
    log.info(
        f"  {agent}: tools={'web' if tools else 'none'}"
    )
    return _run_cli(system_prompt, user_message, tools)


def call_execution_agent(packet: dict) -> str:
    """Call Execution Agent (no web tools — synthesises from research outputs only)."""
    return call_agent("execution", packet)
