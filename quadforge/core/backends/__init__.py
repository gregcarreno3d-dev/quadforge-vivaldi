"""Remeshing engines behind one small interface.

FORGE is QuadForge's own engine (a bundled executable, quads or triangles).
QuadriFlow is Blender's built-in engine (quads only).
Phase 2 can add engines that run in an isolated worker process (QuadWild,
Instant Meshes, ...) by implementing ``Backend`` without touching the pipeline.
"""
from __future__ import annotations

from .base import Backend, BackendError
from .forge import ForgeBackend
from .quadriflow import QuadriFlowBackend

_REGISTRY: dict[str, type[Backend]] = {
    ForgeBackend.id: ForgeBackend,
    QuadriFlowBackend.id: QuadriFlowBackend,
}


def get_backend(backend_id: str) -> Backend:
    try:
        return _REGISTRY[backend_id]()
    except KeyError as exc:
        raise BackendError(f"Unknown backend '{backend_id}'") from exc


__all__ = ["Backend", "BackendError", "get_backend"]
