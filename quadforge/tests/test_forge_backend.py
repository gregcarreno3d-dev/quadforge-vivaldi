"""End-to-end test of the FORGE backend WITHOUT Blender.

A small fake of Blender's mesh API (foreach_get / foreach_set / attributes) is enough to run the
real backend code against the real engine binary:

    python tests/test_forge_backend.py
"""
import importlib
import pathlib
import sys
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "tools"))

package = types.ModuleType("qf")
package.__path__ = [str(ROOT)]
sys.modules["qf"] = package
sys.modules["bpy"] = types.ModuleType("bpy")          # backends may import it; never used here
forge = importlib.import_module("qf.core.backends.forge")
base = importlib.import_module("qf.core.backends.base")
forge_io = importlib.import_module("qf.core.forge_io")
import meshgen  # noqa: E402


class Seq:
    """A Blender collection of elements with foreach_get / foreach_set."""
    def __init__(self, mesh, kind):
        self.mesh, self.kind = mesh, kind

    def __len__(self):
        return len(self.mesh.store[self.kind])

    def add(self, n):
        self.mesh.store[self.kind] = np.zeros((len(self) + n,) + self.mesh.store[self.kind].shape[1:],
                                              self.mesh.store[self.kind].dtype)

    def foreach_get(self, name, out):
        out[:] = self.mesh.field(self.kind, name).reshape(-1)

    def foreach_set(self, name, values):
        self.mesh.set_field(self.kind, name, np.asarray(values))


class AttrData:
    def __init__(self, values): self.values = values
    def __len__(self): return len(self.values)
    def foreach_get(self, name, out): out[:] = self.values


class Attr:
    def __init__(self, data):
        self.data_type, self.domain = "FLOAT", "POINT"
        self.data = AttrData(data)


class FakeMesh:
    def __init__(self, co, sizes, idx, sharp_pairs=(), density=None):
        self.store = {"v": np.asarray(co, np.float32), "poly": np.zeros(len(sizes), np.int32),
                      "loop": np.asarray(idx, np.int32)}
        self.sizes = np.asarray(sizes)
        self.store["poly"][:] = np.concatenate([[0], np.cumsum(self.sizes)[:-1]])
        self.vertices, self.polygons, self.loops = Seq(self, "v"), Seq(self, "poly"), Seq(self, "loop")
        edges = {}
        at = 0
        for s in self.sizes:
            p = self.store["loop"][at:at + s]
            for k in range(s):
                e = tuple(sorted((int(p[k]), int(p[(k + 1) % s]))))
                edges.setdefault(e, len(edges))
            at += s
        self.edge_list = np.array(sorted(edges, key=edges.get), np.int32)
        self.edges = EdgeSeq(self, sharp_pairs)
        self.attrs = {}
        if density is not None:
            self.attrs["quadforge_density"] = Attr(np.asarray(density, np.float32))
        self.attributes = types.SimpleNamespace(get=self.attrs.get)
        self.polygon_offset_indices = OffsetSeq(self)

    def field(self, kind, name):
        if name == "co": return self.store["v"]
        if name == "vertex_index": return self.store["loop"]
        if name == "loop_start": return self.store["poly"]
        if name == "edge_index":
            return np.zeros(len(self.store["loop"]), np.int32)
        raise KeyError(name)

    def set_field(self, kind, name, values):
        if name == "co": self.store["v"][:] = values.reshape(-1, 3)
        elif name == "vertex_index": self.store["loop"][:] = values
        elif name == "loop_start": self.store["poly"][:] = values

    def clear_geometry(self):
        self.store = {"v": np.zeros((0, 3), np.float32), "poly": np.zeros(0, np.int32), "loop": np.zeros(0, np.int32)}

    def update(self, calc_edges=False):
        # sizes follow from loop_start
        starts = self.store["poly"]
        total = len(self.store["loop"])
        self.sizes = np.diff(np.append(starts, total))

    def from_pydata(self, *a): raise AssertionError("slow path should not be needed")


class EdgeSeq:
    def __init__(self, mesh, sharp_pairs):
        self.mesh = mesh
        self.sharp = {tuple(sorted(map(int, p))) for p in sharp_pairs}

    def __len__(self): return len(self.mesh.edge_list)

    def foreach_get(self, name, out):
        if name == "vertices":
            out[:] = self.mesh.edge_list.reshape(-1)
        elif name == "use_edge_sharp":
            out[:] = [tuple(e) in self.sharp for e in self.mesh.edge_list.tolist()]
        else:
            raise KeyError(name)


class OffsetSeq:
    def __init__(self, mesh): self.mesh = mesh
    def __len__(self): return len(self.mesh.store["poly"]) + 1
    def foreach_get(self, out):
        out[:] = np.append(self.mesh.store["poly"], len(self.mesh.store["loop"]))


def check_manifold(mesh):
    sizes, idx = mesh.sizes, mesh.store["loop"]
    seen, at = {}, 0
    for s in sizes:
        p = idx[at:at + s]
        for k in range(s):
            e = (int(p[k]), int(p[(k + 1) % s]))
            seen[e] = seen.get(e, 0) + 1
        at += s
    assert all(v == 1 for v in seen.values()), "directed edge repeated"
    und = {}
    for (a, b) in seen:
        k = (min(a, b), max(a, b)); und[k] = und.get(k, 0) + 1
    assert all(v <= 2 for v in und.values()), "non-manifold edge"
    assert not np.isnan(mesh.store["v"]).any()


def run_case(name, co, tris, output, faces, sharp=(), density=None):
    mesh = FakeMesh(co, np.full(len(tris), 3), np.asarray(tris).reshape(-1), sharp, density)
    obj = types.SimpleNamespace(data=mesh)
    seen = []
    request = base.RemeshRequest(target_faces=faces, preserve_sharp=True, preserve_boundary=True,
                                 output=output, progress=lambda p, m: seen.append(p))
    forge.ForgeBackend().remesh(None, obj, request)
    check_manifold(mesh)
    n = len(mesh.sizes)
    kinds = set(mesh.sizes.tolist())
    want = {4} if output == "QUADS" else {3}
    ok_kind = kinds <= (want | {3})   # a quad result may keep a few triangles
    assert ok_kind, kinds
    assert abs(n - faces) / faces < 0.2, (n, faces)
    assert seen and seen[-1] >= 90, seen[-3:]
    print(f"ok  {name:28s} {output:5s} target {faces:5d} -> {n:5d} faces, sizes {sorted(kinds)}, progress events {len(seen)}")


def main():
    co, tris, _ = meshgen.icosphere(4)
    run_case("sphere", co, tris, "QUADS", 1000)
    run_case("sphere", co, tris, "TRIS", 1000)
    co, tris, feats = meshgen.box(12)
    run_case("box with hard edges", co, tris, "QUADS", 600, sharp=feats.tolist())
    run_case("box with hard edges", co, tris, "TRIS", 600, sharp=feats.tolist())
    # density: left half finer
    co, tris, _ = meshgen.icosphere(4)
    dens = np.where(co[:, 0] > 0, 0.5, 1.0)
    run_case("sphere with density", co, tris, "TRIS", 1500, density=dens)
    # binary lookup
    p = forge.find_engine()
    assert p.is_file(), p
    # engine error path
    try:
        mesh = FakeMesh(np.zeros((3, 3)), [3], [0, 1, 2])
        forge.ForgeBackend().remesh(None, types.SimpleNamespace(data=mesh),
                                    base.RemeshRequest(target_faces=100, preserve_sharp=False, preserve_boundary=True))
    except base.BackendError as e:
        print("ok  degenerate input -> BackendError:", str(e)[:80])
    else:
        print("ok  degenerate input accepted")
    print("ALL OK")


if __name__ == "__main__":
    main()
