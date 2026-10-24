"""Optional file requirements, separate from scientific computation identity."""

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from mic3_api.application.models.errors import ModelError


@dataclass(frozen=True)
class OutputRequirements:
    required_files: tuple[str, ...] = ()

    @classmethod
    def from_list(cls, value: object) -> "OutputRequirements":
        if not isinstance(value, list):
            raise ModelError("required_outputs must be a list of relative file paths.")
        for path in value:
            if not isinstance(path, str) or not path:
                raise ModelError("Required output paths must be nonempty strings.")
            parsed = PurePosixPath(path)
            if (parsed.is_absolute() or PureWindowsPath(path).drive
                    or path == "." or ".." in parsed.parts or str(parsed) != path
                    or any(char in path for char in "\\*?[]")
                    or any(ord(char) < 32 for char in path)):
                raise ModelError("Required outputs must be normalized relative file paths without wildcards.")
        if len(set(value)) != len(value):
            raise ModelError("Required output paths must be unique.")
        # Requirement order has no meaning; reordering must not block registration.
        return cls(tuple(sorted(value)))
