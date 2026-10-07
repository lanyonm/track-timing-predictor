"""botocore exception handling shared by the DynamoDB backends.

botocore is optional for local development, so this module defines stand-ins
when it isn't installed.
"""

try:
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # boto3 not installed (local dev without AWS deps)

    class ClientError(Exception):  # type: ignore[no-redef]
        """Never raised; lets `except ClientError` and isinstance checks run without botocore."""

        response: dict = {}

    BotoError: tuple[type[Exception], ...] = ()
    NoCredentialsError = None  # type: ignore[assignment,misc]
else:
    BotoError = (BotoCoreError, ClientError)

_AUTH_ERROR_CODES = frozenset(
    {
        "ExpiredTokenException",
        "UnrecognizedClientException",
        "AccessDeniedException",
        "InvalidSignatureException",
    }
)


def raise_if_auth_error(exc: Exception) -> None:
    """Re-raise credential/config errors that should not be silently caught."""
    if NoCredentialsError is not None and isinstance(exc, NoCredentialsError):
        raise exc
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _AUTH_ERROR_CODES:
            raise exc
