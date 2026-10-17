"""Domain exceptions for SMC-SNR ML Perpetual Setup Scanner.
"""


class ScannerBaseError(Exception):
    """Base exception for all domain errors."""
    pass


class ExchangeAPIError(ScannerBaseError):
    """Raised when an exchange API call fails or returns an error."""
    pass


class ExchangeRateLimitError(ExchangeAPIError):
    """Raised when an exchange rate limit or HTTP 429 is encountered."""
    pass


class SymbolNotFoundError(ExchangeAPIError):
    """Raised when a requested symbol is not found or unsupported by the exchange."""
    pass


class InvalidCandleError(ScannerBaseError):
    """Raised when candle data fails structural or chronological validation."""
    pass


class LookaheadBiasError(ScannerBaseError):
    """Raised when an operation attempts to access future data."""
    pass


class StaleDataError(ScannerBaseError):
    """Raised when market data is too old to produce safe execution decisions."""
    pass


class InsufficientDataError(ScannerBaseError):
    """Raised when candle history or sample size is inadequate for modeling or setups."""
    pass


class InfeasibleOrderError(ScannerBaseError):
    """Raised when order sizing cannot be fulfilled within risk budget or exchange lot rules."""
    pass


class RiskLimitExceededError(ScannerBaseError):
    """Raised when daily, weekly, or account exposure limits are breached."""
    pass


class ConfigurationError(ScannerBaseError):
    """Raised when an invalid configuration is provided."""
    pass


class InvalidOrderStateTransitionError(ScannerBaseError):
    """Raised when an order attempts an illegal lifecycle transition."""
    pass

