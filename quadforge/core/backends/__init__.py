"""Remeshing engines behind one small interface.

FORGE is QuadForge's own engine (a bundled executable, quads or triangles). It is the only engine.
Another engine can be added by implementing ``Backend`` without touching the pipeline.
"""
from __future__ import annotations

from .base import Backend, BackendError
from .forge import ForgeBackend

_REGISTRY: dict[str, type[Backend]] = {
    ForgeBackend.id: ForgeBackend,
}


def get_backend(backend_id: str = ForgeBackend.id) -> Backend:
    try:
        return _REGISTRY[backend_id]()
    except KeyError as exc:
        raise BackendError(f"Unknown backend '{backend_id}'") from exc


__all__ = ["Backend", "BackendError", "get_backend"]
