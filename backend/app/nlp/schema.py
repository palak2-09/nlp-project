import json
import os
from pathlib import Path
import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import BACKEND_ROOT, PROJECT_ROOT


class ExtractionSchema(BaseModel):
    name: str = "papergraph-default-v1"
    entity_types: list[str] = Field(min_length=1)
    relationship_types: list[str] = Field(min_length=1)
    entity_cues: dict[str, str] = Field(default_factory=dict)
    relationship_patterns: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("entity_types", "relationship_types")
    @classmethod
    def unique_nonempty_labels(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Schema labels must not be empty.")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Schema labels must be unique.")
        return normalized

    @model_validator(mode="after")
    def validate_cues_and_patterns(self):
        entity_labels = {label.casefold() for label in self.entity_types}
        relation_labels = set(self.relationship_types)
        if any(label.casefold() not in entity_labels for label in self.entity_cues.values()):
            raise ValueError("Every entity cue must point to a configured entity type.")
        if any(predicate not in relation_labels for predicate in self.relationship_patterns):
            raise ValueError("Every relationship pattern must use a configured relationship type.")
        try:
            for patterns in self.relationship_patterns.values():
                for pattern in patterns:
                    re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Relationship pattern is invalid: {exc}") from exc
        return self


def load_schema(schema_path: Path | None = None) -> ExtractionSchema:
    configured_path = schema_path or (Path(os.environ["PAPERGRAPH_SCHEMA_PATH"]).expanduser() if os.getenv("PAPERGRAPH_SCHEMA_PATH") else None)
    if configured_path and not configured_path.is_absolute():
        if configured_path.parts[:1] == ("backend",):
            configured_path = BACKEND_ROOT / Path(*configured_path.parts[1:])
        else:
            configured_path = PROJECT_ROOT / configured_path
    path = configured_path or (Path(__file__).with_name("schema.json"))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExtractionSchema.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"The extraction schema at {path} is missing or invalid.") from exc
