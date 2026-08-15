from django.conf import settings


def build_backend_url(path: str) -> str:
    """Return an absolute API URL using the configured public backend host."""
    return f"{settings.BACKEND_URL.rstrip('/')}/{path.lstrip('/')}"


def build_frontend_url(path: str) -> str:
    """Build a URL for either a web frontend or the SeedVest mobile scheme."""
    base_url = settings.FRONTEND_URL.strip()
    if base_url.endswith("://"):
        return f"{base_url}{path.lstrip('/')}"
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"
