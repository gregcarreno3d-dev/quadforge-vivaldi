from __future__ import annotations

import bpy

from .base import Backend, BackendError, RemeshRequest


class QuadriFlowBackend(Backend):
    """Blender's built-in QuadriFlow (``object.quadriflow_remesh``)."""

    id = "QUADRIFLOW"
    label = "QuadriFlow (built-in)"

    def remesh(self, context, obj, request: RemeshRequest) -> None:
        op = bpy.ops.object.quadriflow_remesh
        # Operator options change between Blender versions: only pass the ones
        # this build actually has, so a renamed option never crashes the add-on.
        available = {p.identifier for p in op.get_rna_type().properties}
        wanted = {
            "mode": "FACES",
            "target_faces": int(request.target_faces),
            "use_mesh_symmetry": False,   # symmetry is handled by our own cut/mirror
            "use_preserve_sharp": request.preserve_sharp,
            "use_preserve_boundary": request.preserve_boundary,
            "smooth_normals": False,
            "seed": int(request.seed),
        }
        kwargs = {k: v for k, v in wanted.items() if k in available}
        if "target_faces" not in kwargs or "mode" not in kwargs:
            raise BackendError("This Blender build has no QuadriFlow face-count mode.")

        # The working object must be the only selected/active one.
        for other in list(context.view_layer.objects):
            if other is not None:          # a stale entry can appear right after a scene reset
                other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            result = op("EXEC_DEFAULT", **kwargs)
        except RuntimeError as exc:  # operator reported an error
            raise BackendError(f"QuadriFlow failed: {exc}") from exc
        if "FINISHED" not in result:
            raise BackendError(
                "QuadriFlow did not finish. Common causes: non-manifold geometry, "
                "loose parts or a target that is too low for the sharp/boundary constraints."
            )
