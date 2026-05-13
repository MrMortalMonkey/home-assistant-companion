# =============================================================================
# LLM PROVIDER — Abstraction layer for multiple LLM backends
# Supported: anthropic, openai, openrouter, ollama, lmstudio
# =============================================================================

import json
import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

# Provider registry
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic Claude",
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-haiku-4-5-20251001",
        "default_model_strong": "claude-sonnet-4-6",
    },
    "openai": {
        "name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "default_model_strong": "gpt-4o",
    },
    "openrouter": {
        "name": "OpenRouter",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-3.5-haiku",
        "default_model_strong": "anthropic/claude-3.5-sonnet",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "name": "Ollama (local)",
        "env_key": "OLLAMA_HOST",
        "default_model": "llama3.2",
        "default_model_strong": "llama3.2",
        "base_url": "http://localhost:11434",
    },
    "lmstudio": {
        "name": "LM Studio (local)",
        "env_key": "LMSTUDIO_HOST",
        "default_model": "local-model",
        "default_model_strong": "local-model",
        "base_url": "http://localhost:1234",
    },
}


def get_provider_config(cfg):
    """Returns the active provider configuration from global config."""
    provider_name = cfg.get("llm_provider", "anthropic")
    return PROVIDERS.get(provider_name, PROVIDERS["anthropic"])


def get_api_key(cfg):
    """Returns the API key for the configured provider."""
    provider_name = cfg.get("llm_provider", "anthropic")
    provider = PROVIDERS.get(provider_name, PROVIDERS["anthropic"])
    key_field = provider.get("env_key", "ANTHROPIC_API_KEY")

    if provider_name == "ollama":
        return cfg.get("ollama_host", provider.get("base_url", "http://localhost:11434"))
    if provider_name == "lmstudio":
        return cfg.get("lmstudio_host", provider.get("base_url", "http://localhost:1234"))
    if provider_name == "openai":
        return cfg.get("openai_api_key", "")
    if provider_name == "openrouter":
        return cfg.get("openrouter_api_key", "")
    return cfg.get("anthropic_api_key", "")


def get_model(cfg, use_strong=False):
    """Returns the model name for the configured provider."""
    provider_name = cfg.get("llm_provider", "anthropic")
    provider = PROVIDERS.get(provider_name, PROVIDERS["anthropic"])
    key = "default_model_strong" if use_strong else "default_model"
    model_override = cfg.get(f"llm_model_{'strong_' if use_strong else ''}{provider_name}")
    return model_override or provider[key]


def _openai_compatible_chat(cfg, messages, model, max_tokens, system_prompt=None, tools=None, temperature=0):
    """Generic call for OpenAI-compatible APIs (OpenAI, OpenRouter, Ollama, LMStudio)."""
    import requests as _requests

    provider_name = cfg.get("llm_provider", "anthropic")
    provider = PROVIDERS.get(provider_name, PROVIDERS["anthropic"])
    api_key = get_api_key(cfg)

    if provider_name == "ollama":
        base_url = cfg.get("ollama_host", provider.get("base_url", "http://localhost:11434"))
    elif provider_name == "lmstudio":
        base_url = cfg.get("lmstudio_host", provider.get("base_url", "http://localhost:1234"))
    elif provider_name == "openrouter":
        base_url = provider.get("base_url", "https://openrouter.ai/api/v1")
    else:
        base_url = "https://api.openai.com/v1"

    if provider_name == "ollama":
        # Ollama uses slightly different API
        url = f"{base_url.rstrip('/')}/api/chat"
        ollama_messages = []
        if system_prompt:
            ollama_messages.append({"role": "system", "content": system_prompt})
        ollama_messages.extend(messages)
        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": temperature}
        }
        if tools:
            payload["tools"] = tools
        headers = {}
    else:
        url = f"{base_url.rstrip('/')}/chat/completions"
        oa_messages = []
        if system_prompt:
            oa_messages.append({"role": "system", "content": system_prompt})
        oa_messages.extend(messages)
        payload = {
            "model": model,
            "messages": oa_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        if provider_name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/MrMortalMonkey/home-assistant-companion"
            headers["X-Title"] = "Home Assistant AI Companion"

    try:
        r = _requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code != 200:
            log.error(f"[{provider_name}] HTTP {r.status_code}: {r.text[:200]}")
            return None, 0, 0

        data = r.json()

        if provider_name == "ollama":
            content = data.get("message", {}).get("content", "")
            return [{"type": "text", "text": content}], data.get("prompt_eval_count", 0), data.get("eval_count", 0)
        else:
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            usage = data.get("usage", {})
            t_in = usage.get("prompt_tokens", 0)
            t_out = usage.get("completion_tokens", 0)

            result_blocks = []
            if content:
                result_blocks.append({"type": "text", "text": content})
            for tc in tool_calls:
                try:
                    func_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                except json.JSONDecodeError:
                    func_args = {}
                result_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "input": func_args,
                })
            return result_blocks, t_in, t_out

    except Exception as e:
        log.error(f"[{provider_name}] API error: {e}")
        return None, 0, 0


def _anthropic_chat(cfg, messages, model, max_tokens, system_prompt=None, tools=None, temperature=0):
    """Native Anthropic API call with cache control support."""
    import anthropic as _anth

    api_key = get_api_key(cfg)
    if not api_key:
        log.error("Anthropic API key not configured")
        return None, 0, 0

    try:
        client = _anth.Anthropic(api_key=api_key)

        anthropic_kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }

        if system_prompt:
            anthropic_kwargs["system"] = [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ]

        if tools:
            anthropic_kwargs["tools"] = tools

        for tentative in range(4):
            try:
                r = client.messages.create(**anthropic_kwargs)
                t_in = r.usage.input_tokens
                t_out = r.usage.output_tokens
                t_cache_read = getattr(r.usage, "cache_read_input_tokens", 0)
                t_cache_write = getattr(r.usage, "cache_creation_input_tokens", 0)
                total_in = t_in + t_cache_read + t_cache_write
                log.debug(f"Tokens: in={t_in} out={t_out} cache_r={t_cache_read} cache_w={t_cache_write}")
                return r.content, total_in, t_out
            except _anth.RateLimitError:
                wait = (tentative + 1) * 15
                log.warning(f"Rate limit, retry in {wait}s (attempt {tentative + 1}/4)")
                time.sleep(wait)
            except _anth.APIStatusError as e:
                if "overloaded" in str(e).lower():
                    wait = (tentative + 1) * 10
                    log.warning(f"API overloaded, retry in {wait}s")
                    time.sleep(wait)
                else:
                    raise
        log.error("Anthropic API: 4 attempts failed")
        return None, 0, 0
    except _anth.AuthenticationError:
        log.error("Invalid Anthropic API key")
        raise
    except _anth.BadRequestError as e:
        log.warning(f"Anthropic BadRequest: {str(e)[:100]}")
        raise
    except Exception as e:
        log.error(f"Anthropic API error: {e}")
        return None, 0, 0


def llm_completion(cfg, messages, model=None, max_tokens=1000, system_prompt=None, temperature=0):
    """Unified completion call across all providers.
    
    Returns (content_blocks, tokens_in, tokens_out) where content_blocks is a list
    of dicts with 'type' and 'text' keys.
    """
    provider_name = cfg.get("llm_provider", "anthropic")
    if model is None:
        model = get_model(cfg)

    if provider_name == "anthropic":
        blocks, t_in, t_out = _anthropic_chat(
            cfg, messages, model, max_tokens, system_prompt=system_prompt, temperature=temperature
        )
    else:
        blocks, t_in, t_out = _openai_compatible_chat(
            cfg, messages, model, max_tokens, system_prompt=system_prompt, temperature=temperature
        )

    return blocks, t_in, t_out


def llm_completion_with_tools(cfg, messages, tools, model=None, max_tokens=2000, system_prompt=None, temperature=0):
    """Unified completion call with tool/function support."""
    provider_name = cfg.get("llm_provider", "anthropic")
    if model is None:
        model = get_model(cfg, use_strong=("sonnet" in str(model or "").lower() or "gpt-4" in str(model or "").lower()))

    if provider_name == "anthropic":
        return _anthropic_chat(
            cfg, messages, model, max_tokens, system_prompt=system_prompt, tools=tools, temperature=temperature
        )
    else:
        return _openai_compatible_chat(
            cfg, messages, model, max_tokens, system_prompt=system_prompt, tools=tools, temperature=temperature
        )


def stream_text(content_blocks):
    """Extract plain text from content blocks (unified format)."""
    text = ""
    if content_blocks is None:
        return text
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
        elif hasattr(block, "type") and block.type == "text":
            text += block.text
    return text


def extract_tool_use(content_blocks):
    """Extract tool use blocks from content blocks (unified format)."""
    tools = []
    if content_blocks is None:
        return tools
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "tool_use":
                tools.append(block)
        elif hasattr(block, "type") and block.type == "tool_use":
            tools.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return tools


def dictify_content_blocks(blocks):
    """Convert Anthropic message objects to plain dicts for uniform handling."""
    if blocks is None:
        return None
    result = []
    for block in blocks:
        if isinstance(block, dict):
            result.append(block)
        else:
            btype = getattr(block, "type", "text")
            entry = {"type": btype}
            if btype == "text":
                entry["text"] = getattr(block, "text", "")
            elif btype == "tool_use":
                entry["id"] = getattr(block, "id", "")
                entry["name"] = getattr(block, "name", "")
                entry["input"] = getattr(block, "input", {})
            result.append(entry)
    return result
