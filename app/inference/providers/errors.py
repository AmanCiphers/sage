class ProviderError(Exception):
    """Base error for inference provider failures."""


class ProviderAPIError(ProviderError):
    """The provider returned an HTTP error."""


class ProviderResponseError(ProviderError):
    """The provider returned an invalid response."""


class ProviderTimeoutError(ProviderError):
    """The provider request timed out."""
