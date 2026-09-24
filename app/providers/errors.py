class ProviderError(RuntimeError):
    """Safe, user-facing provider error without exposing secrets."""

class ProviderConfigurationError(ProviderError):
    pass
