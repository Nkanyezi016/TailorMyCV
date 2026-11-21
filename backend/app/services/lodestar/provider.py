import asyncio
import json
from importlib.resources import files
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import Field, ValidationError

from app.config import ApiError, Settings
from app.schemas import Schema

SYSTEM_PROMPT = files(__package__).joinpath("system_prompt.md").read_text(encoding="utf-8")


class Plan(Schema):
    evidence_order: list[UUID]
    summary_evidence_ids: list[UUID] = Field(max_length=3)


class Provider(Protocol):
    async def plan(self, context: dict) -> Plan: ...


class OpenAIProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def plan(self, context: dict) -> Plan:
        schema = Plan.model_json_schema()
        # Structured Outputs accepts the UUIDs as strings. Pydantic validates them locally.
        for name in ("evidence_order", "summary_evidence_ids"):
            schema["properties"][name] = {"type": "array", "items": {"type": "string"}}
        request = {
            "model": self.settings.openai_model,
            "store": False,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "cv_evidence_plan",
                    "schema": schema,
                    "strict": True,
                },
            },
            "max_output_tokens": 16_384,
        }
        for attempt in range(self.settings.provider_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.client.post(
                        "https://api.openai.com/v1/responses",
                        json=request,
                        headers={
                            "Authorization": "Bearer "
                            + self.settings.openai_api_key.get_secret_value(),
                        },
                    ),
                    timeout=self.settings.provider_timeout,
                )
                if response.status_code in {408, 429} or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Temporary provider error", request=response.request, response=response
                    )
                if not response.is_success:
                    raise ApiError(502, "provider_rejected", "Lodestar could not process this run.")
                return self.parse(response.json())
            except (TimeoutError, httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == self.settings.provider_retries:
                    raise ApiError(
                        503, "provider_unavailable", "Lodestar is unavailable. Try a new run later."
                    ) from exc
                await asyncio.sleep(0.25 * (2**attempt))
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise ApiError(
                    502, "invalid_provider_output", "Lodestar returned an invalid evidence plan."
                ) from exc
        raise RuntimeError("Provider retry loop ended unexpectedly")

    @staticmethod
    def parse(payload: dict) -> Plan:
        try:
            if payload["status"] != "completed":
                raise ValueError("Incomplete response")
            outputs = [
                content["text"]
                for item in payload["output"]
                if item["type"] == "message"
                for content in item["content"]
                if content["type"] == "output_text"
            ]
            if len(outputs) != 1:
                raise ValueError("Expected one structured response")
            return Plan.model_validate_json(outputs[0])
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            raise ApiError(
                502, "invalid_provider_output", "Lodestar returned an invalid evidence plan."
            ) from exc
