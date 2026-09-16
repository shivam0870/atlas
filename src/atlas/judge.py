import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from atlas.config import settings
from atlas.generation import plan_chat


class Judgment(BaseModel):
    supported_claims: int = Field(ge=0)
    total_claims: int = Field(ge=0)
    reason: str

    @model_validator(mode="after")
    def valid_counts(self):
        if self.supported_claims > self.total_claims:
            raise ValueError("Supported claims cannot exceed total claims")
        return self


async def judge_answer(answer: str, sources: list[dict]):
    prompt = await asyncio.to_thread(Path("prompts/faithfulness.md").read_text)
    data = json.dumps(
        {"answer": answer, "evidence": [s["content"] for s in sources]}, ensure_ascii=False
    )
    if len((prompt + data).encode()) > 7000:
        return {"score": None, "reason": "Judge context limit exceeded", "calibrated": False}
    response = await plan_chat(
        model=settings.generation_model,
        stream=False,
        format=Judgment.model_json_schema(),
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": data}],
        options={"num_ctx": 8192, "num_predict": 400, "temperature": 0},
    )
    result = Judgment.model_validate_json(response.message.content or "")
    return {
        **result.model_dump(),
        "score": result.supported_claims / result.total_claims if result.total_claims else None,
        "calibrated": False,
    }
