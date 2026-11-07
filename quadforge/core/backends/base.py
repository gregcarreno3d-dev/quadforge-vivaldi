from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BackendError(RuntimeError):
    """Raised when an engine fails; the pipeline cleans up and reports it."""


@dataclass(frozen=True)
class RemeshRequest:
    target_faces: int
    preserve_sharp: bool
    preserve_boundary: bool
    seed: int = 0
    output: str = "QUADS"          # QUADS | TRIS (only engines that can do both look at it)
    threads: int = 0               # 0 = engine decides
    progress: object = None        # optional callable(percent, message) fed by the engine
    curv_angle: float = 0.0        # >0: smaller faces on strongly curved areas (radians a face edge may turn the surface)
    guides: object = None          # optional (n, 2, 3) float array: flow guide segments in object space (soft hints for the quad rows)
    fast_seams: bool = False       # only guide lines (symmetry planes) are marked: skip the engine's second grid solve
    engine_info: dict = field(default_factory=dict)       # filled by the engine backend: the engine's final statistics (method, fallback reason)


class Backend(ABC):
    id: str = ""
    label: str = ""

    @abstractmethod
    def remesh(self, context, obj, request: RemeshRequest) -> None:
        """Quad-remesh ``obj.data`` in place.

        ``obj`` is a temporary working object that is already active and
        selected. Sharp edges marked on its mesh (``use_edge_sharp``) are the
        feature constraints the engine must respect.
        """

    def remesh_steps(self, context, obj, request: RemeshRequest):
        """Generator version of ``remesh``: yields while the engine works so the caller can stay responsive.

        The default simply runs ``remesh`` (engines that cannot be interrupted). Closing the
        generator early (user pressed Esc) must stop any process the engine started.
        """
        self.remesh(context, obj, request)
        yield
