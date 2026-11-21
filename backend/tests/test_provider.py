import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.config import ApiError, Settings
from app.services.lodestar.provider import OpenAIProvider


def test_structured_provider_retries_temporary_errors():
    evidence_id = str(uuid4())
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        if attempts == 1:
            return httpx.Response(429)
        plan = {"evidence_order": [evidence_id], "summary_evidence_ids": [evidence_id]}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(plan),
                            }
                        ],
                    }
                ],
            },
        )

    async def run():
        settings = Settings(provider="openai", openai_api_key="test-key", provider_retries=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await OpenAIProvider(settings, client).plan({"evidence": [evidence_id]})

    result = asyncio.run(run())
    assert attempts == 2
    assert str(result.evidence_order[0]) == evidence_id


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "evidence_order": [],
                                    "summary_evidence_ids": [],
                                    "invented_skill": "Power BI",
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    ],
)
def test_invalid_provider_response(payload):
    with pytest.raises(ApiError) as error:
        OpenAIProvider.parse(payload)
    assert error.value.code == "invalid_provider_output"
