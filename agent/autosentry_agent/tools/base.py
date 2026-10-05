"""A tool the investigation agent can call: name, description, input schema, risk.

`invoke` takes the model's raw arguments, checks them against the input
model before anything reaches TigerGraph, and never raises for a bad call
or a failed one: the agent gets an `ok: false` result it can act on.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ..mcp.transport import GraphDataToolError
from .results import InvestigationResult, failure
from .tool_risk import Risk, classify


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Any], Awaitable[InvestigationResult]]

    @property
    def risk(self) -> Risk:
        return classify(self.name)

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    async def invoke(self, arguments: dict[str, Any]) -> InvestigationResult:
        try:
            params = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return failure(f"{self.name} was called with invalid arguments", problems)
        try:
            return await self.run(params)
        except GraphDataToolError as exc:
            return failure(f"{self.name} could not run", str(exc))
