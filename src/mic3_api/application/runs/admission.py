"""Admission bounds for authenticated submissions, independent of persistence."""
from dataclasses import dataclass


class RunAdmissionExceeded(Exception):
    """Outstanding work has reached an API admission bound."""


@dataclass(frozen=True)
class RunAdmissionLimits:
    per_user: int
    global_total: int

    def __post_init__(self):
        if self.per_user < 1 or self.global_total < 1:
            raise ValueError("Run admission limits must be positive.")

    def check(self, user_count: int, total_count: int) -> None:
        if user_count >= self.per_user:
            raise RunAdmissionExceeded("Your outstanding run limit has been reached.")
        if total_count >= self.global_total:
            raise RunAdmissionExceeded("The platform outstanding run limit has been reached.")
