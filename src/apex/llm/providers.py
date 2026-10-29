"""LLM access for the proposal steps. Two ways to reach a model, one interface:

- an API key for a service (`anthropic`, `openai`), read from the environment, never from
  apex.toml, so the config can be committed;
- a coding agent CLI the user already has installed and logged in (`claude-code`, `codex`), run
  headless with its tools switched off, in an empty temp directory, so it answers from the prompt
  alone and spends the user's existing subscription rather than a separate key.

(`host` is the third mode and needs no provider here: the agent that is driving Apex writes the
proposals itself and hands them to `apex intent submit`.)

Every provider returns JSON that matches a schema. What comes back is only ever a proposal:
callers validate it (`check_proposals`) and nothing a model says can produce a verdict.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol

from apex.core.config import ApexConfig

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"


class ProviderError(RuntimeError):
    """The model could not be reached or returned nothing usable. Maps to a tool error."""


class Provider(Protocol):
    name: str
    """Recorded as `proposed_by` (provider, plus model when one was chosen)."""

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def _loads(text: str, who: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProviderError(f"{who} did not return JSON: {e}: {text[:200]!r}") from e
    if not isinstance(data, dict):
        raise ProviderError(f"{who} returned {type(data).__name__}, expected an object")
    return data


# ---- API key providers -------------------------------------------------------------------------


class AnthropicProvider:
    def __init__(self, model: str | None, timeout_s: float) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise ProviderError(
                "install the extra: `uv tool install 'apex-verify[anthropic]'`"
            ) from e
        self._anthropic = anthropic
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
        self._client = anthropic.Anthropic(timeout=timeout_s)
        self.model = model or ANTHROPIC_DEFAULT_MODEL
        self.name = f"anthropic:{self.model}"

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        anthropic = self._anthropic
        try:
            # Server-side fallback keeps a safety-classifier decline from ending the run; the
            # fallback model's answer goes through the same validation as any other proposal.
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ProviderError(
                "Anthropic rejected the credentials: set ANTHROPIC_API_KEY or run `ant auth login`"
            ) from e
        except anthropic.RateLimitError as e:
            raise ProviderError(f"Anthropic rate limit: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"cannot reach the Anthropic API: {e}") from e
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise ProviderError("the model's answer was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _loads(text, self.name)


class OpenAIProvider:
    def __init__(self, model: str | None, timeout_s: float) -> None:
        try:
            import openai
        except ImportError as e:
            raise ProviderError("install the extra: `uv tool install 'apex-verify[openai]'`") from e
        if model is None:
            # Which OpenAI model to use is part of the open provider decision; Apex does not pick.
            raise ProviderError('set `model = "..."` in apex.toml to use the openai provider')
        self._openai = openai
        self._client = openai.OpenAI(timeout=timeout_s)  # reads OPENAI_API_KEY
        self.model = model
        self.name = f"openai:{model}"

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        openai = self._openai
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=system,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "apex_proposals",
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
        except openai.AuthenticationError as e:
            raise ProviderError("OpenAI rejected the credentials: set OPENAI_API_KEY") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAI API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise ProviderError(f"cannot reach the OpenAI API: {e}") from e
        return _loads(response.output_text, self.name)


# ---- coding agent CLIs -------------------------------------------------------------------------


def _run_cli(cmd: list[str], prompt: str, timeout_s: float, cwd: Path, who: str) -> str:
    try:
        out = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, cwd=cwd
        )
    except subprocess.TimeoutExpired as e:
        raise ProviderError(f"{who} did not answer within {timeout_s:.0f}s") from e
    except OSError as e:
        raise ProviderError(f"cannot run {who}: {e}") from e
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()
        raise ProviderError(f"{who} exited {out.returncode}: {detail[-1] if detail else ''}")
    return out.stdout


class ClaudeCodeProvider:
    """`claude -p` with every tool disabled and structured output enforced by the CLI."""

    def __init__(self, model: str | None, timeout_s: float) -> None:
        exe = shutil.which("claude")
        if exe is None:
            raise ProviderError("the `claude` CLI is not on PATH (install Claude Code and log in)")
        self.exe, self.model, self.timeout_s = exe, model, timeout_s
        self.name = "claude-code" + (f":{model}" if model else "")

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        cmd = [
            self.exe,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--tools",
            "",
            "--system-prompt",
            system,
            "--no-session-persistence",
        ]
        if self.model:
            cmd += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="apex-llm-") as tmp:
            raw = _run_cli(cmd, prompt, self.timeout_s, Path(tmp), "claude")
        envelope = _loads(raw, "claude")
        if envelope.get("is_error"):
            raise ProviderError(f"claude reported an error: {envelope.get('result')}")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return structured
        result = envelope.get("result")
        if isinstance(result, str):
            return _loads(result, "claude")
        raise ProviderError("claude returned no structured output")


class CodexProvider:
    """`codex exec` in a read-only sandbox, with the answer constrained by `--output-schema`."""

    def __init__(self, model: str | None, timeout_s: float) -> None:
        exe = shutil.which("codex")
        if exe is None:
            raise ProviderError("the `codex` CLI is not on PATH (install Codex and log in)")
        self.exe, self.model, self.timeout_s = exe, model, timeout_s
        self.name = "codex" + (f":{model}" if model else "")

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="apex-llm-") as tmp:
            schema_path, out_path = Path(tmp) / "schema.json", Path(tmp) / "answer.json"
            schema_path.write_text(json.dumps(schema))
            cmd = [
                self.exe,
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "-o",
                str(out_path),
            ]
            if self.model:
                cmd += ["--model", self.model]
            cmd.append("-")
            _run_cli(cmd, f"{system}\n\n{prompt}", self.timeout_s, Path(tmp), "codex")
            if not out_path.is_file():
                raise ProviderError("codex wrote no answer")
            return _loads(out_path.read_text(), "codex")


PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
}


def get_provider(config: ApexConfig, override: str | None = None) -> Provider:
    name = override or config.model_provider
    if name == "none":
        raise ProviderError(
            "no model provider configured: set model_provider in apex.toml (anthropic, openai, "
            "claude-code, codex), pass --provider, or let your coding agent propose hints "
            "itself with `apex intent submit` (host mode)"
        )
    if name == "host":
        raise ProviderError(
            "model_provider is `host`: the agent driving Apex writes the proposals and submits "
            "them with `apex intent submit`; Apex does not call a model itself"
        )
    if name not in PROVIDERS:
        raise ProviderError(f"unknown provider `{name}`; choose one of {', '.join(PROVIDERS)}")
    model = config.model if override is None or override == config.model_provider else None
    return PROVIDERS[name](model, config.llm_timeout_s)


def available_agent_clis() -> dict[str, str | None]:
    """For `apex doctor`: which coding agent CLIs could serve as a provider."""
    return {"claude-code": shutil.which("claude"), "codex": shutil.which("codex")}


def api_keys_present() -> dict[str, bool]:
    return {
        "anthropic": bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
    }
