"""Validated catalog execution choices, independent of model language and transport."""

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re

from mic3_api.application.models.errors import ModelError


def validate_key(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ModelError("Model and mode identifiers must be lowercase path-safe names of up to 63 characters.")
    return value


@dataclass(frozen=True)
class Invocation:
    arguments: tuple[str, ...]
    working_directory: str
    output_directory: str

    @classmethod
    def from_dict(cls, value: object) -> "Invocation":
        fields = {"arguments", "working_directory", "output_directory"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ModelError("Execution definition requires arguments, working_directory and output_directory.")
        arguments = value["arguments"]
        if not isinstance(arguments, list) or not arguments or any(
            not isinstance(arg, str) or not arg or "\0" in arg for arg in arguments
        ):
            raise ModelError("Execution arguments must be a nonempty list of strings.")
        for field in ("working_directory", "output_directory"):
            path = value[field]
            if (not isinstance(path, str) or "\0" in path
                    or not PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
                raise ModelError("Execution directories must be absolute container paths.")
        return cls(tuple(arguments), value["working_directory"], value["output_directory"])

    def to_dict(self) -> dict:
        return {**asdict(self), "arguments": list(self.arguments)}


@dataclass(frozen=True)
class ExecutionDefinition:
    modes: dict[str, Invocation]

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionDefinition":
        if not isinstance(value, dict) or set(value) != {"modes"}:
            raise ModelError("Execution definition requires a modes object.")
        modes = value["modes"]
        if not isinstance(modes, dict) or not modes:
            raise ModelError("At least one catalog mode is required.")
        return cls({validate_key(key): Invocation.from_dict(invocation) for key, invocation in modes.items()})

    def to_dict(self) -> dict:
        return {"modes": {key: invocation.to_dict() for key, invocation in self.modes.items()}}

    def prepare(self, parameters: object) -> dict:
        # Only mode selection is supported today. Add scientific input preparation
        # when an actual model requires it; do not silently accept ignored fields.
        if not isinstance(parameters, dict) or set(parameters) != {"mode"}:
            raise ModelError("Exactly the mode parameter is required.")
        mode = parameters["mode"]
        if not isinstance(mode, str) or mode not in self.modes:
            raise ModelError("Unsupported mode; supported modes: " + ", ".join(sorted(self.modes)) + ".")
        return {"mode": mode}

    def resolve(self, parameters: dict) -> Invocation:
        return self.modes[self.prepare(parameters)["mode"]]

    def parameter_metadata(self) -> dict:
        return {
            "type": "object", "additionalProperties": False, "required": ["mode"],
            "properties": {"mode": {"type": "string", "enum": sorted(self.modes)}},
        }
