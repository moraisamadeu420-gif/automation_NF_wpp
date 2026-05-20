"""
Adapter registry — maps municipality keys to concrete adapter instances.
Add new municipalities here after implementing their adapter class.
"""
from app.adapters.base_adapter import BaseNfseAdapter
from app.adapters.campinas_adapter import CampinasAdapter
from app.adapters.nacional_adapter import NacionalAdapter

_REGISTRY: dict[str, BaseNfseAdapter] = {
    adapter.municipality_key: adapter
    for adapter in [
        NacionalAdapter(),
        CampinasAdapter(),
    ]
}


def get_adapter(municipality_key: str) -> BaseNfseAdapter:
    from app.core.exceptions import AdapterNotFoundError

    key = municipality_key.lower().strip()
    adapter = _REGISTRY.get(key)
    if not adapter:
        raise AdapterNotFoundError(municipality_key)
    return adapter


def list_available_municipalities() -> list[str]:
    return list(_REGISTRY.keys())
