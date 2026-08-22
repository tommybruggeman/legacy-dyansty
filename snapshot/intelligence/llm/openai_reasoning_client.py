import os
import json
from typing import Any, Dict

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(".env")


def has_openai_key() -> bool:
    key = os.getenv("OPENAI_API_KEY")
    return bool(key and key.startswith("sk-"))


class OpenAIReasoningClient:
    def __init__(self, model: str = "gpt-5.4-mini"):
        if not has_openai_key():
            raise RuntimeError("OPENAI_API_KEY is not set or invalid.")
        self.client = OpenAI(timeout=20.0)
        self.model = model

    def reason_json(self, system_prompt: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            text={"format": {"type": "json_object"}},
        )
        return json.loads(response.output_text)
