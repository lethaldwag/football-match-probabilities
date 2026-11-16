"""Provider registry. To add a provider: write an adapter and register it here."""

from app.config import Settings, get_settings
from app.providers.base import ProviderAdapter, ProviderError
from app.providers.football_data_org import FootballDataOrgAdapter
from app.providers.football_data_uk import FootballDataUkAdapter
from app.providers.openfootball import OpenFootballAdapter

PROVIDER_NAMES = (FootballDataUkAdapter.name, OpenFootballAdapter.name, FootballDataOrgAdapter.name)


def get_adapter(name: str, settings: Settings | None = None) -> ProviderAdapter:
    settings = settings or get_settings()
    if name == FootballDataUkAdapter.name:
        return FootballDataUkAdapter(bookmakers=tuple(settings.csv(settings.fduk_bookmakers)))
    if name == OpenFootballAdapter.name:
        return OpenFootballAdapter()
    if name == FootballDataOrgAdapter.name:
        return FootballDataOrgAdapter(api_key=settings.football_data_org_api_key)
    raise ProviderError(f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}")
