"""Domain and Application Exceptions for Secure Model Serving and Champion Loading."""

from __future__ import annotations


class ModelServingError(Exception):
    """Base exception for model serving and inference errors."""

    def __init__(self, message: str, code: str = "MODEL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ModelNotAvailableError(ModelServingError):
    """Raised when no verified champion model artifact is available to serve."""

    def __init__(
        self,
        message: str = "A verified champion model is not currently available.",
        code: str = "MODEL_NOT_READY",
    ) -> None:
        super().__init__(message, code=code)


class ModelIntegrityError(ModelServingError):
    """Raised when a model artifact or cache payload fails cryptographic integrity or authentication."""

    def __init__(
        self,
        message: str = "Model artifact failed integrity or authenticity verification.",
        code: str = "MODEL_INTEGRITY_ERROR",
    ) -> None:
        super().__init__(message, code=code)


class ModelCompatibilityError(ModelServingError):
    """Raised when model weights or architecture are incompatible."""

    def __init__(
        self,
        message: str = "Model artifact is incompatible with serving architecture.",
        code: str = "MODEL_COMPATIBILITY_ERROR",
    ) -> None:
        super().__init__(message, code=code)


class ModelExecutionError(ModelServingError):
    """Raised when model inference execution produces non-finite or invalid outputs."""

    def __init__(
        self,
        message: str = "Model inference output violated numerical or schema contract.",
        code: str = "MODEL_EXECUTION_ERROR",
    ) -> None:
        super().__init__(message, code=code)
