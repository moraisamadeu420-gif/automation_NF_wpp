"""
app/core/exceptions.py
Domain-specific exceptions for controlled error propagation.
"""


class NfseBotError(Exception):
    """Base error for all application exceptions."""


class CredentialNotFoundError(NfseBotError):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"NFSe credentials not configured for user {user_id}")
        self.user_id = user_id


class AdapterNotFoundError(NfseBotError):
    def __init__(self, municipality: str) -> None:
        super().__init__(f"No adapter registered for municipality: {municipality}")
        self.municipality = municipality


class NfseEmissionError(NfseBotError):
    """Raised when the NFSe portal automation fails."""

    def __init__(
        self,
        message: str,
        stage: str = "",
        critical: bool = False,
        screenshot_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.critical = critical
        self.screenshot_path = screenshot_path


class EvolutionApiError(NfseBotError):
    """Raised when Evolution API returns an unexpected response."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
