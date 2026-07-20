"""Application-specific exceptions for the expense tracker integration.

Every failure that reaches the user is expressed as one of these types so that
handlers can translate it into a short, friendly message without ever exposing
stack traces, API keys, or internal server details.
"""


class ExpenseTrackerError(Exception):
    """Base error for every expense tracker failure.

    ``user_message`` is the only text that should ever be surfaced to the user.
    Subclasses override it with a more specific, still-sanitized message.
    """

    user_message = "The expense tracker is temporarily unavailable. Please try again."


class ExpenseValidationError(ExpenseTrackerError):
    """The request was rejected (locally or by the API) as invalid (HTTP 400)."""

    def __init__(self, user_message: str | None = None):
        message = user_message or "I couldn't do that because some details were invalid."
        super().__init__(message)
        self.user_message = message


class ExpenseAuthenticationError(ExpenseTrackerError):
    """The API key is missing or rejected (HTTP 401)."""

    user_message = "The expense tracker is not configured correctly."


class ExpenseNotFoundError(ExpenseTrackerError):
    """The requested expense does not exist (HTTP 404)."""

    user_message = "I couldn't find that expense. It may already have been deleted."


class ExpenseServiceUnavailableError(ExpenseTrackerError):
    """A network error, timeout, or 5xx response occurred."""

    user_message = "The expense tracker is temporarily unavailable. Please try again."
