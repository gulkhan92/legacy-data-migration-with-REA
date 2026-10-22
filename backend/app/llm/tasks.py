"""The two LLM tasks: versioned prompt files, JSON output schemas, and checks of the answer against the request.

A prompt is a file `prompts/<task>.v<N>.txt` that is the constant first part of every request (so provider prompt caching
applies). The versioned file name and its SHA-256 are part of the cache key, so changing a prompt can never serve answers
produced by an older one. The variable part is the JSON payload, sent as the user message.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.llm.errors import SchemaFailure

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
LAYOUT_TYPES = {"char", "int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64", "bytes"}


# ------------------------------------------------------------------ output schemas (strict: every field required)


class MappingAnswer(BaseModel):
    id: str
    target: str | None
    confidence: float = Field(ge=0, le=1)
    hint: str | None


class ResolveMappingsOutput(BaseModel):
    answers: list[MappingAnswer]


class LayoutAnswer(BaseModel):
    id: str
    type: str
    length: int | None
    endianness: str | None
    confidence: float = Field(ge=0, le=1)


class InterpretLayoutOutput(BaseModel):
    answers: list[LayoutAnswer]


# ------------------------------------------------------------------ checks of an answer against its request


def check_resolve_mappings(output: ResolveMappingsOutput, payload: dict[str, Any]) -> None:
    columns = {c["id"]: {k["key"] for k in c["candidates"]} for c in payload["columns"]}
    answered = [a.id for a in output.answers]
    problems = []
    if set(answered) != set(columns) or len(answered) != len(set(answered)):
        problems.append(f"give exactly one answer for each of the ids {sorted(columns)}")
    for a in output.answers:
        if a.id in columns and a.target is not None and a.target not in columns[a.id]:
            problems.append(f"{a.target} is not one of the candidates of {a.id}: {sorted(columns[a.id])}")
    if problems:
        raise SchemaFailure("; ".join(problems))


def check_interpret_layout(output: InterpretLayoutOutput, payload: dict[str, Any]) -> None:
    wanted = {a["id"] for a in payload["ambiguities"]}
    problems = []
    if {a.id for a in output.answers} != wanted or len(output.answers) != len(wanted):
        problems.append(f"give exactly one answer for each of the ids {sorted(wanted)}")
    for a in output.answers:
        if a.type not in LAYOUT_TYPES:
            problems.append(f"type {a.type} is not one of {sorted(LAYOUT_TYPES)}")
        if a.endianness not in (None, "little", "big"):
            problems.append("endianness must be little, big or null")
        if a.length is not None and not 1 <= a.length <= 4096:
            problems.append("length must be between 1 and 4096")
    if problems:
        raise SchemaFailure("; ".join(problems))


# ------------------------------------------------------------------ the task registry


@dataclass(frozen=True)
class Prompt:
    task: str
    version: int
    text: str
    sha256: str

    @property
    def label(self) -> str:
        return f"{self.task}.v{self.version}"


@dataclass(frozen=True)
class TaskSpec:
    name: str
    version: int
    output: type[BaseModel]
    check: Callable[[Any, dict[str, Any]], None]

    @property
    def prompt(self) -> Prompt:
        return load_prompt(self.name, self.version)


TASKS: dict[str, TaskSpec] = {
    "resolve_mappings": TaskSpec("resolve_mappings", 1, ResolveMappingsOutput, check_resolve_mappings),
    "interpret_layout": TaskSpec("interpret_layout", 1, InterpretLayoutOutput, check_interpret_layout),
}


@lru_cache
def load_prompt(task: str, version: int) -> Prompt:
    path = PROMPT_DIR / f"{task}.v{version}.txt"
    text = path.read_text(encoding="utf-8").strip()
    return Prompt(task, version, text, hashlib.sha256(text.encode()).hexdigest())


def latest_version(task: str) -> int:
    versions = [int(m.group(1)) for p in PROMPT_DIR.glob(f"{task}.v*.txt") if (m := re.fullmatch(rf"{task}\.v(\d+)\.txt", p.name))]
    return max(versions)


# ------------------------------------------------------------------ JSON schemas for the providers


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A JSON schema in the form strict structured outputs require: every property required, no extra properties,
    optional values as a union with null, no references."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            if "anyOf" in node:
                options = [resolve(o) for o in node["anyOf"]]
                kinds = [o.get("type") for o in options]
                if len(options) == 2 and "null" in kinds:
                    other = next(o for o in options if o.get("type") != "null")
                    return {**{k: v for k, v in other.items() if k != "title"}, "type": [other["type"], "null"]}
                return {"anyOf": options}
            out = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}))
            return out
        if isinstance(node, list):
            return [resolve(n) for n in node]
        return node

    return resolve(schema)


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The same schema in the OpenAPI subset of the Gemini API: upper case types, nullable instead of a union with null,
    no additionalProperties."""

    def convert(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key == "additionalProperties":
                    continue
                if key == "type":
                    kinds = value if isinstance(value, list) else [value]
                    real = [k for k in kinds if k != "null"]
                    out["type"] = real[0].upper()
                    if "null" in kinds:
                        out["nullable"] = True
                elif key in ("minimum", "maximum"):
                    out[key] = value
                else:
                    out[key] = convert(value)
            return out
        if isinstance(node, list):
            return [convert(n) for n in node]
        return node

    return convert(schema)
