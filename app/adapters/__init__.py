from app.adapters.base_adapter import BaseNfseAdapter
from app.adapters.nacional_adapter import NacionalAdapter

_REGISTRY: dict[str, BaseNfseAdapter] = {
    NacionalAdapter().municipality_key: NacionalAdapter(),
}


def get_adapter(municipality_key: str) -> BaseNfseAdapter:
    from app.core.exceptions import AdapterNotFoundError

    adapter = _REGISTRY.get(municipality_key.lower().strip())
    if not adapter:
        raise AdapterNotFoundError(municipality_key)
    return adapter
