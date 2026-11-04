"""Research prototype: measure what kind of model an STL is (hard surface, organic, or mixed).

This is NOT part of the add-on and needs no Blender: it only needs NumPy and works on binary STL files.
It produced the numbers in docs/PRESETS_AND_WORKFLOW_STUDY.md (sections 6 and 13) and is kept so anyone can
repeat or extend the study with their own models.

    python tests/research_model_analysis.py model1.stl model2.stl
    python tests/research_model_analysis.py model.stl --sensitivity --health

What it prints for each model:
  * size and topology (triangles, shells, open and non-manifold edges)
  * dihedral-angle statistics and the share of edges that are creases (> 35 degrees)
  * planar patches (share of the area that is flat) and how stable that number is (--sensitivity)
  * crease chains: how many are long and how much crease length lies in them
  * local classes: share of the area that is planar / near a hard edge / smooth / rough
  * mirror match about the bounding-box centre (candidate symmetry axes)
  * model health: tiny shells and non-manifold edges (--health)
  * the provisional classification rule from the study

Binary STL only. Thresholds are PROVISIONAL: they were tuned on three real models.
"""
import argparse
import pathlib
import struct
import sys
import time

import numpy as np

def load_stl(path):
    data = pathlib.Path(path).read_bytes(); n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data[84:], dtype=np.dtype([("n","<f4",3),("v","<f4",(3,3)),("a","<u2")]), count=n)
    return rec["v"].astype(np.float64)

def components(n_nodes, ea, eb):
    labels = np.arange(n_nodes)
    while True:
        la, lb = labels[ea], labels[eb]; lo = np.minimum(la, lb)
        new = labels.copy(); np.minimum.at(new, la, lo); np.minimum.at(new, lb, lo); new = new[new]
        if np.array_equal(new, labels): return labels
        labels = new

class Model:
    def __init__(self, name):
        tris = load_stl(name)
        pts = tris.reshape(-1,3)
        self.v, inv = np.unique(np.round(pts, 5), axis=0, return_inverse=True)
        self.f = inv.reshape(-1,3); n_v, n_f = len(self.v), len(self.f); self.n_v, self.n_f = n_v, n_f
        v, f = self.v, self.f
        a,b,c = v[f[:,0]], v[f[:,1]], v[f[:,2]]
        nr = np.cross(b-a, c-a); ar = 0.5*np.linalg.norm(nr,axis=1); ok = ar > 1e-14
        nr[ok] /= (2*ar[ok])[:,None]; self.nr, self.ar, self.area = nr, ar, ar.sum()
        e = np.sort(np.concatenate([f[:,[0,1]], f[:,[1,2]], f[:,[2,0]]]), axis=1); face_of = np.tile(np.arange(n_f),3)
        uk, einv, cnt = np.unique(e[:,0].astype(np.int64)*n_v + e[:,1], return_inverse=True, return_counts=True)
        order = np.argsort(einv, kind="stable"); sf = face_of[order]; st = np.cumsum(cnt)-cnt
        self.edges = np.stack([uk//n_v, uk%n_v],1); self.cnt = cnt; self.two = np.flatnonzero(cnt==2)
        self.f1, self.f2 = sf[st[self.two]], sf[st[self.two]+1]
        self.ang = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", nr[self.f1], nr[self.f2]), -1, 1)))
        self.elen = np.linalg.norm(v[self.edges[self.two,0]]-v[self.edges[self.two,1]], axis=1)
        self.n_e = len(uk)
        self.lo, self.hi = v.min(0), v.max(0); self.diag = np.linalg.norm(self.hi-self.lo)

    def topology(self):
        lab = components(self.n_v, self.f[:,0], self.f[:,1]); lab = components(self.n_v, np.concatenate([self.f[:,0],self.f[:,1],self.f[:,2]]), np.concatenate([self.f[:,1],self.f[:,2],self.f[:,0]]))
        shells = len(np.unique(lab[self.f[:,0]]))
        chi = self.n_v - self.n_e + self.n_f
        return dict(shells=shells, open_edges=int((self.cnt==1).sum()), nonmanifold=int((self.cnt>2).sum()), euler=int(chi))

    def planar(self, flat_deg=2.0, dev_deg=4.0, min_area=0.01):
        flat = self.ang < flat_deg
        lab = components(self.n_f, self.f1[flat], self.f2[flat]); _, ci = np.unique(lab, return_inverse=True); ci = ci.reshape(-1)
        nc = ci.max()+1; mean_n = np.zeros((nc,3)); np.add.at(mean_n, ci, self.nr*self.ar[:,None])
        mean_n /= np.maximum(np.linalg.norm(mean_n,axis=1),1e-30)[:,None]
        dev = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", self.nr, mean_n[ci]), -1, 1)))
        comp_area = np.bincount(ci, weights=self.ar, minlength=nc); comp_dev = np.zeros(nc); np.maximum.at(comp_dev, ci, dev)
        planar = (comp_dev < dev_deg) & (comp_area > min_area*self.area)
        return float(comp_area[planar].sum()/self.area), int(planar.sum()), ci, planar

    def creases(self, angle=35.0, target_quads=20000, long_chain_quads=8):
        L = np.sqrt(self.area/target_quads); s = self.ang > angle
        if not s.any(): return dict(strong=0, strong_pct=0.0, coherent_share=0.0, coherent_density=0.0, right_in_coherent=0.0, chains=0, long_chains=0, right_edges=0, strong_mask=s)
        idx = self.two[s]; lab = components(self.n_v, self.edges[idx,0], self.edges[idx,1]); _, ci = np.unique(lab[self.edges[idx,0]], return_inverse=True); ci = ci.reshape(-1)
        clen = np.bincount(ci, weights=self.elen[s]); coh = clen >= long_chain_quads*L; in_coh = coh[ci]; right = np.abs(self.ang[s]-90) <= 15
        return dict(strong=int(s.sum()), strong_pct=100*float(s.mean()), coherent_share=float(self.elen[s][in_coh].sum()/self.elen[s].sum()),
                    coherent_density=float(self.elen[s][in_coh].sum()/np.sqrt(self.area)), right_in_coherent=float(right[in_coh].mean()) if in_coh.any() else 0.0,
                    chains=int(len(clen)), long_chains=int(coh.sum()), right_edges=int(right.sum()), strong_mask=s)

    def local_classes(self, planar_faces):
        """Area share of each face class: planar patch / near a hard edge / smooth / other."""
        worst = np.zeros(self.n_f); np.maximum.at(worst, self.f1, self.ang); np.maximum.at(worst, self.f2, self.ang)
        hard_adj = worst > 35; smooth = worst < 10
        planar_f = planar_faces
        cls_planar = planar_f
        cls_hard = hard_adj & ~planar_f
        cls_smooth = smooth & ~planar_f & ~hard_adj
        cls_other = ~(cls_planar | cls_hard | cls_smooth)
        t = self.area
        return {k: float(self.ar[m].sum()/t) for k, m in (("planar", cls_planar), ("near hard edge", cls_hard), ("smooth", cls_smooth), ("other (rough/mid)", cls_other))}

    def symmetry(self, tol_rel=2e-3, samples=2000):
        ctr = (self.lo+self.hi)/2; cell = tol_rel*self.diag
        keys = set(map(tuple, np.floor(self.v/cell).astype(np.int64)))
        rng = np.random.default_rng(0); idx = rng.choice(self.n_v, size=min(samples, self.n_v), replace=False); out = {}
        offs = [(i,j,k) for i in (-1,0,1) for j in (-1,0,1) for k in (-1,0,1)]
        for ax, L in enumerate("XYZ"):
            m = self.v[idx].copy(); m[:,ax] = 2*ctr[ax]-m[:,ax]; base = np.floor(m/cell).astype(np.int64); hit = 0
            for row in base:
                if any((row[0]+o[0], row[1]+o[1], row[2]+o[2]) in keys for o in offs): hit += 1
            out[L] = hit/len(idx)
        return out

def classify(planar_area, right_in_coherent, coherent_share, strong_pct):
    """The provisional rule proposed in docs/PRESETS_AND_WORKFLOW_STUDY.md (section 6.4)."""
    if planar_area >= 0.50: return "Hard Surface"
    if strong_pct < 2.0 and planar_area < 0.05: return "Organic"
    if right_in_coherent >= 0.60 and coherent_share >= 0.80: return "Hard Surface"
    return "Mixed"


def health_report(m):
    lab = components(m.n_v, np.concatenate([m.f[:,0], m.f[:,1], m.f[:,2]]), np.concatenate([m.f[:,1], m.f[:,2], m.f[:,0]]))
    roots, counts = np.unique(lab[m.f[:,0]], return_counts=True)
    print("   shells (triangles each): " + str(sorted(counts.tolist(), reverse=True)))
    for root, count in sorted(zip(roots, counts), key=lambda item: -item[1])[1:6]:
        used = np.unique(m.f[lab[m.f[:,0]] == root]); points = m.v[used]
        print(f"   small shell with {count:,} triangles at {np.round(points.min(0),1)} to {np.round(points.max(0),1)}")
    bad = m.edges[m.cnt > 2]
    if len(bad):
        mid = 0.5 * (m.v[bad[:,0]] + m.v[bad[:,1]])
        print(f"   {len(bad)} non-manifold edges, e.g. at {np.round(mid[:3], 2).tolist()}")


def sensitivity_report(m):
    print("   planar share (patches) for flat-edge angle / normal deviation / minimum patch area:")
    for flat in (1.0, 2.0, 5.0):
        row = []
        for dev in (3.0, 4.0, 10.0):
            share, patches, *_ = m.planar(flat_deg=flat, dev_deg=dev, min_area=0.01)
            row.append(f"dev {dev:>2.0f}: {100*share:5.1f}% ({patches})")
        print(f"      flat {flat:.0f} deg  " + "   ".join(row))
    share, patches, *_ = m.planar(2.0, 4.0, 0.005)
    print(f"      minimum patch area 0.5% instead of 1%: {100*share:.1f}% ({patches})")


def roughness_share(m):
    worst = np.zeros(m.n_f); np.maximum.at(worst, m.f1, m.ang); np.maximum.at(worst, m.f2, m.ang)
    band = (worst >= 10) & (worst <= 35)
    return float(m.ar[band].sum() / m.area)


def main():
    parser = argparse.ArgumentParser(description="Classify STL models (research prototype)")
    parser.add_argument("files", nargs="+", help="binary STL files")
    parser.add_argument("--target-quads", type=int, default=20000, help="target used to scale the crease-chain filter")
    parser.add_argument("--sensitivity", action="store_true", help="show how stable the planar detector is")
    parser.add_argument("--health", action="store_true", help="list tiny shells and non-manifold edges")
    arguments = parser.parse_args()
    for path in arguments.files:
        started = time.time(); m = Model(path)
        print(f"\n=================== {pathlib.Path(path).name} ===================")
        print(f"triangles {m.n_f:,} (about {m.n_f//2:,} quads) | vertices {m.n_v:,} | surface area {m.area:.2f} | diagonal {m.diag:.2f}")
        print(f"topology: {m.topology()}")
        mean_edge = m.elen.mean()
        print(f"mean edge length {100*mean_edge/m.diag:.3f}% of the diagonal | edge-length variation {m.elen.std()/mean_edge:.2f}")
        print(f"dihedral angle: median {np.median(m.ang):.2f} | p90 {np.percentile(m.ang,90):.1f} | >35 deg {100*np.mean(m.ang>35):.1f}% | >80 deg {100*np.mean(m.ang>80):.1f}%")
        share, patches, ci, planar_mask = m.planar()
        print(f"planar patches: {patches} covering {100*share:.1f}% of the area")
        creases = m.creases(target_quads=arguments.target_quads)
        print(f"creases > 35 deg: {creases['strong']:,} edges ({creases['strong_pct']:.1f}%), {creases['chains']} chains, {creases['long_chains']} long | "
              f"coherent share {100*creases['coherent_share']:.0f}% | right-angle edges {creases['right_edges']:,} ({100*creases['right_in_coherent']:.0f}% of coherent chains)")
        print("local classes (share of area): " + ", ".join(f"{k} {100*v:.1f}%" for k, v in m.local_classes(planar_mask[ci]).items()))
        print(f"roughness (area whose sharpest edge is 10-35 deg): {100*roughness_share(m):.1f}%")
        print("mirror match about the bbox centre: " + ", ".join(f"{k} {100*v:.0f}%" for k, v in m.symmetry().items()))
        if arguments.sensitivity: sensitivity_report(m)
        if arguments.health: health_report(m)
        print(f"classification (study rule v1): {classify(share, creases['right_in_coherent'], creases['coherent_share'], creases['strong_pct'])}   [{time.time()-started:.1f} s]")


if __name__ == "__main__":
    main()
