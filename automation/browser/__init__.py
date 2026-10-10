"""Browser and provider-session lifecycle primitives."""

from .manager import (
    BrowserLaunchError,
    BrowserManager,
    BrowserManagerError,
    ProfileInUseError,
)
from .sessions import (
    PROFILE_ROOT_ENV,
    SUPPORTED_PROVIDERS,
    SessionCloseError,
    SessionManager,
    SessionManagerError,
    UnknownProviderError,
    UnsafeProfileRootError,
    default_profile_root,
    resolve_profile_root,
)

__all__ = [
    "PROFILE_ROOT_ENV",
    "SUPPORTED_PROVIDERS",
    "BrowserLaunchError",
    "BrowserManager",
    "BrowserManagerError",
    "ProfileInUseError",
    "SessionCloseError",
    "SessionManager",
    "SessionManagerError",
    "UnknownProviderError",
    "UnsafeProfileRootError",
    "default_profile_root",
    "resolve_profile_root",
]
