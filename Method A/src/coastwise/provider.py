from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx


class ModelError(RuntimeError):
    pass


class RateLimited(ModelError):
    """HTTP 429: rejected before generation, so nothing was billed."""


class OpenRouter:
    """Small HTTP adapter. Credentials never enter run metadata or request logs."""

    def __init__(self, api_key: str, model: str, provider: str = "", max_usd: float = 1.0,
                 max_tokens: int = 4000, max_total_tokens: int = 150000,
                 transport=None, max_attempts: int = 3, timeout_seconds: int = 120,
                 capture_responses: bool = False, ledger=None):
        self.api_key, self.model, self.provider = api_key, model, provider
        self.max_usd, self.max_tokens = max_usd, max_tokens
        self.max_total_tokens = max_total_tokens
        self.spent = 0.0
        self.reserved = 0.0
        self.tokens = 0
        self.logs: list[dict] = []
        self.pricing: dict | None = None
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.max_attempts = max_attempts
        self.ledger = ledger
        self.capture_responses = capture_responses
        self.responses: list[dict] = []
        self.http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", timeout=timeout_seconds,
                                     transport=transport)

    async def close(self):
        await self.http.aclose()
        self.api_key = ""

    async def models(self) -> list[dict]:
        resp = await self.http.get("models")
        resp.raise_for_status()
        return resp.json()["data"]

    async def preflight(self, tools: bool = False):
        if not self.api_key:
            raise ModelError("Add an OpenRouter API key or configure OPENROUTER_API_KEY on the server.")
        catalog = await self.models()
        selected = next((m for m in catalog if m["id"] == self.model), None)
        if selected is None:
            raise ModelError("Model not found in the OpenRouter catalog. Refresh models and select an exact ID.")
        supported = set(selected.get("supported_parameters", []))
        if not ({"response_format", "structured_outputs"} & supported):
            raise ModelError("Selected model does not advertise structured output support.")
        if tools and "tools" not in supported:
            raise ModelError("Method B with loop requires a model that supports tools.")
        self.pricing = selected.get("pricing")
        if not self.pricing or any(self.pricing.get(k) is None for k in ("prompt", "completion")):
            raise ModelError("Model pricing is unavailable, so the run's cost cap cannot be checked.")
        # Explicit temperature only when supported; unsupported parameters must not be ignored.
        self.temperature_supported = "temperature" in supported
        self.reasoning_supported = "reasoning" in supported or "reasoning_effort" in supported
        if self.ledger is not None:
            await self.preflight_budget_endpoint()
        overrides = self.pricing.get("overrides", [])
        self.budget_pricing = {name: max(float(self.pricing.get(name, 0)),
            *(float(p.get(name, self.pricing.get(name, 0))) for p in overrides)) if overrides else float(self.pricing.get(name, 0))
            for name in ("prompt", "completion", "request")}

    async def preflight_budget_endpoint(self):
        """Pin a verified route and reserve for its entire possible input context."""
        response = await self.http.get(f"models/{self.model}/endpoints")
        response.raise_for_status()
        candidates = [e for e in response.json()["data"]["endpoints"] if e.get("tag") == self.provider]
        if len(candidates) != 1:
            raise ModelError("A unique OpenRouter endpoint tag is required for the durable budget")
        endpoint = candidates[0]
        if "structured_outputs" not in endpoint.get("supported_parameters", []):
            raise ModelError("The pinned OpenRouter endpoint does not advertise JSON Schema support")
        self.pricing = endpoint["pricing"]
        self.context_length = int(endpoint["context_length"])
        if self.context_length < self.max_tokens or not all(k in self.pricing for k in ("prompt", "completion")):
            raise ModelError("The pinned endpoint lacks a usable context or pricing bound")

    async def call(self, messages: list[dict], schema: dict | None = None,
                   tools: list[dict] | None = None) -> dict:
        if self.pricing is None:
            raise ModelError("Run preflight before making a model request")
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens,
                "provider": {"require_parameters": True, "allow_fallbacks": False},
                "stream": False}
        if self.provider:
            body["provider"]["only"] = [self.provider]
        if getattr(self, "temperature_supported", False):
            body["temperature"] = 0
        if getattr(self, "reasoning_effort", None) and getattr(self, "reasoning_supported", False):
            body["reasoning"] = {"enabled": False} if self.reasoning_effort == "none" else {"effort": self.reasoning_effort}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "inspection_extraction", "strict": True, "schema": schema}}
        if tools:
            body["tools"] = tools
        # UTF-8 byte length is a conservative token allowance, including schema/tool payload.
        allowance = len(json.dumps(body).encode()) + self.max_tokens
        prices = getattr(self, "budget_pricing", self.pricing)
        if self.ledger is not None:
            # OpenRouter's routing limits are in dollars per million tokens, except per request.
            body["provider"]["max_price"] = {
                "prompt": prices["prompt"] * 1_000_000,
                "completion": prices["completion"] * 1_000_000,
                "request": prices.get("request", 0),
            }
            # Cached input can also be billed. Use the higher published per-token rate.
            prompt_rate = max(float(tier.get(key, 0)) for tier in (self.pricing, *self.pricing.get("overrides", []))
                              for key in ("prompt", "input_cache_read", "input_cache_write"))
            estimate = self.context_length * prompt_rate + self.max_tokens * prices["completion"] + prices["request"]
        else:
            estimate = ((allowance - self.max_tokens) * float(prices["prompt"]) +
                        self.max_tokens * float(prices["completion"]) + float(prices.get("request", 0)))
        for attempt in range(self.max_attempts):
            if self.reserved + estimate > self.max_usd or self.tokens + allowance > self.max_total_tokens:
                raise ModelError("Run budget reached. Raise the cap or use fewer pages/output tokens.")
            started = time.monotonic()
            entry = {"model_requested": self.model, "provider_requested": self.provider or None,
                     "attempt": attempt + 1, "reserved_usd": estimate}
            if getattr(self, "stage", None):
                entry["stage"] = self.stage
            if "reasoning" in body:
                entry["reasoning"] = body["reasoning"]
            reservation = None
            if self.ledger is not None:
                fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                reservation = self.ledger.reserve(estimate, entry, fingerprint)
                entry["ledger_attempt_id"] = reservation
            self.reserved += estimate
            self.tokens += allowance
            try:
                resp = await self.http.post("chat/completions", json=body, headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-OpenRouter-Title": "Coastwise inspection workbench"})
                entry["http_status"] = resp.status_code
                if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_attempts - 1:
                    entry["error"] = "Transient provider error; request reservation retained"
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                if resp.status_code == 429:
                    # No generation happened, so this attempt's reservation is released.
                    self.reserved -= estimate
                    self.tokens -= allowance
                    entry["reserved_usd"] = 0.0
                    raise RateLimited("OpenRouter returned HTTP 429. The request was rate limited and not processed.")
                if resp.is_error:
                    raise ModelError(f"OpenRouter returned HTTP {resp.status_code}. Check model/provider, credits, and key.")
                data = resp.json()
                if self.capture_responses:
                    self.responses.append(data)
                if "error" in data or not data.get("choices"):
                    raise ModelError("OpenRouter returned an error or no completion")
                entry.update({"generation_id": data.get("id"), "model_returned": data.get("model"),
                              "provider_returned": data.get("provider"), "usage": data.get("usage", {})})
                cost = data.get("usage", {}).get("cost")
                if cost is not None:
                    self.spent += float(cost)
                    self.reserved += float(cost) - estimate
                choice = data["choices"][0]
                entry["finish_reason"] = choice.get("finish_reason")
                if choice.get("finish_reason") in ("length", "content_filter", "error"):
                    raise ModelError(f"Completion stopped with {choice['finish_reason']}; partial data was not accepted")
                message = choice.get("message", {})
                if message.get("refusal"):
                    raise ModelError("The model refused this request")
                return message
            except httpx.TransportError as exc:
                entry["error"] = type(exc).__name__
                if attempt == self.max_attempts - 1:
                    raise ModelError(f"OpenRouter connection failed after {self.max_attempts} attempt(s)") from exc
                await asyncio.sleep(0.5 * (2 ** attempt))
            finally:
                entry["seconds"] = round(time.monotonic() - started, 3)
                self.logs.append(entry)
                if reservation is not None:
                    self.ledger.settle(reservation, entry)
        raise ModelError("OpenRouter request failed")


def parse_completion(message: dict) -> dict:
    content = message.get("content")
    if not isinstance(content, str):
        raise ModelError("The model did not return a JSON object")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ModelError("The model returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ModelError("The model returned JSON that was not an object")
    return value
