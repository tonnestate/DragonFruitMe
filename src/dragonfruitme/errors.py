from __future__ import annotations


class DragonFruitMeError(Exception):
    """Structured domain error safe to return to an AI caller."""

    def __init__(self, code: str, message: str, *, recoverable: bool = True, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": self.details,
        }
