//! Turn arbitrary polygon soup into a valid manifold triangle mesh.
//!
//! What it does (and reports):
//! * triangulates polygons (quads along the shorter diagonal),
//! * drops faces that repeat a vertex and exact duplicate faces,
//! * orients every connected piece consistently,
//! * unlinks non-manifold edges (they become open borders, which are preserved),
//! * duplicates "pinched" vertices so every vertex has a single fan of faces,
//! * removes unused vertices.
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use std::collections::HashSet;

#[derive(Clone, Debug, Default)]
pub struct TriSoup {
    pub p: Vec<Vec3>,
    pub f: Vec<[i32; 3]>,
    /// On repaired vertex ids.
    pub features: Vec<[i32; 2]>,
    /// Empty when not given.
    pub density: Vec<f32>,
}

#[derive(Clone, Debug, Default)]
pub struct RepairReport {
    pub input_polygons: i32,
    pub repeated_vertex_faces: i32,
    pub duplicate_faces: i32,
    pub non_manifold_edges: i32,
    pub conflicting_edges: i32,
    pub flipped_faces: i32,
    pub pinched_vertices: i32,
    pub collision_faces: i32,
    pub unused_vertices: i32,
}

impl RepairReport {
    pub fn to_json(&self, faces: usize) -> String {
        format!(
            "{{\"input_polygons\":{},\"repeated_vertex_faces\":{},\"duplicate_faces\":{},\"nonmanifold_edges\":{},\
\"conflicting_edges\":{},\"flipped_faces\":{},\"pinched_vertices\":{},\"collision_faces\":{},\"unused_vertices\":{},\"faces\":{}}}",
            self.input_polygons, self.repeated_vertex_faces, self.duplicate_faces, self.non_manifold_edges,
            self.conflicting_edges, self.flipped_faces, self.pinched_vertices, self.collision_faces,
            self.unused_vertices, faces
        )
    }
}

struct DisjointSet { parent: Vec<usize> }

impl DisjointSet {
    fn new(n: usize) -> Self { DisjointSet { parent: (0..n).collect() } }
    fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            self.parent[x] = self.parent[self.parent[x]];
            x = self.parent[x];
        }
        x
    }
    fn unite(&mut self, a: usize, b: usize) {
        let (a, b) = (self.find(a), self.find(b));
        if a != b { self.parent[a.max(b)] = a.min(b); }
    }
}

/// Unordered vertex pair packed in a u64 (smaller id in the high half).
pub fn edge_key(a: i32, b: i32) -> u64 {
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    ((a as u32 as u64) << 32) | b as u32 as u64
}

#[derive(Clone, Copy)]
struct EdgeRec {
    key: u64,
    face: usize,
    /// The edge is opposite this corner.
    corner: usize,
    /// 0 if the face walks the edge from the smaller to the larger vertex id.
    dir: u8,
}

fn collect_edges(f: &[[i32; 3]]) -> Vec<EdgeRec> {
    let mut e = Vec::with_capacity(f.len() * 3);
    for (fi, t) in f.iter().enumerate() {
        for k in 0..3 {
            let a = t[(k + 1) % 3];
            let b = t[(k + 2) % 3];
            e.push(EdgeRec { key: edge_key(a, b), face: fi, corner: k, dir: if a < b { 0 } else { 1 } });
        }
    }
    e.sort_by(|x, y| x.key.cmp(&y.key).then(x.face.cmp(&y.face)).then(x.corner.cmp(&y.corner)));
    e
}

fn group_edges(edges: &[EdgeRec]) -> Vec<(usize, usize)> {
    let mut groups = Vec::new();
    let mut i = 0;
    while i < edges.len() {
        let mut j = i;
        while j < edges.len() && edges[j].key == edges[i].key { j += 1; }
        groups.push((i, j));
        i = j;
    }
    groups
}

pub fn repair_mesh(input: &PolyMesh, rep: &mut RepairReport) -> TriSoup {
    let mut out = TriSoup { p: input.p.clone(), ..Default::default() };
    let n_v0 = input.p.len();
    rep.input_polygons = input.sizes.len() as i32;

    // 1. triangulate
    let mut f: Vec<[i32; 3]> = Vec::with_capacity(input.sizes.len() * 2);
    let mut at = 0usize;
    for &s in &input.sizes {
        let s = s as usize;
        let v = &input.idx[at..at + s];
        at += s;
        if s == 3 {
            f.push([v[0], v[1], v[2]]);
        } else if s == 4 {
            let d02 = input.p[v[0] as usize].distance2(input.p[v[2] as usize]);
            let d13 = input.p[v[1] as usize].distance2(input.p[v[3] as usize]);
            if d02 <= d13 { f.push([v[0], v[1], v[2]]); f.push([v[0], v[2], v[3]]); }
            else { f.push([v[0], v[1], v[3]]); f.push([v[1], v[2], v[3]]); }
        } else {
            for i in 1..s - 1 { f.push([v[0], v[i], v[i + 1]]); }
        }
    }

    // 2. repeated vertices and duplicate faces
    {
        let mut kept = Vec::with_capacity(f.len());
        for t in &f {
            if t[0] == t[1] || t[1] == t[2] || t[0] == t[2] { rep.repeated_vertex_faces += 1; continue; }
            kept.push(*t);
        }
        let mut sorted: Vec<([i32; 3], usize)> = kept.iter().enumerate().map(|(i, t)| { let mut s = *t; s.sort(); (s, i) }).collect();
        sorted.sort();
        let mut drop = vec![false; kept.len()];
        for i in 1..sorted.len() {
            if sorted[i].0 == sorted[i - 1].0 { drop[sorted[i].1] = true; rep.duplicate_faces += 1; }
        }
        f = kept.iter().enumerate().filter(|(i, _)| !drop[*i]).map(|(_, t)| *t).collect();
    }

    // 3. orientation and edge linking
    let n_f = f.len();
    let edges = collect_edges(&f);
    let groups = group_edges(&edges);
    let mut adj: Vec<Vec<(usize, u8)>> = vec![Vec::new(); n_f];
    for &(b, e) in &groups {
        let n = e - b;
        if n == 2 {
            let (a, c) = (edges[b], edges[b + 1]);
            if a.face != c.face {
                let rel = if a.dir == c.dir { 1 } else { 0 };
                adj[a.face].push((c.face, rel));
                adj[c.face].push((a.face, rel));
            }
        } else if n > 2 {
            rep.non_manifold_edges += 1;
        }
    }
    let mut flip = vec![-1i8; n_f];
    let mut queue: Vec<usize> = Vec::new();
    for s in 0..n_f {
        if flip[s] != -1 { continue; }
        flip[s] = 0;
        queue.clear();
        queue.push(s);
        let mut qi = 0;
        while qi < queue.len() {
            let fc = queue[qi];
            qi += 1;
            for &(nf, rel) in &adj[fc] {
                if flip[nf] == -1 { flip[nf] = flip[fc] ^ rel as i8; queue.push(nf); }
            }
        }
    }
    for fi in 0..n_f {
        if flip[fi] == 1 { f[fi].swap(1, 2); rep.flipped_faces += 1; }
    }
    // after orientation, an edge is linked only if the two faces now walk it in opposite directions
    let edges = collect_edges(&f);
    let groups = group_edges(&edges);
    let mut ds = DisjointSet::new(n_f * 3);
    for &(b, e) in &groups {
        if e - b != 2 { continue; }
        let (a, c) = (edges[b], edges[b + 1]);
        if a.face == c.face { continue; }
        if a.dir == c.dir { rep.conflicting_edges += 1; continue; } // non-orientable: leave open
        ds.unite(a.face * 3 + (a.corner + 1) % 3, c.face * 3 + (c.corner + 2) % 3);
        ds.unite(a.face * 3 + (a.corner + 2) % 3, c.face * 3 + (c.corner + 1) % 3);
    }

    // 4. split pinched vertices: one new vertex per group of linked corners
    let mut root_to_vertex = vec![-1i32; n_f * 3];
    let mut vertex_used = vec![false; n_v0];
    let mut origin: Vec<i32> = (0..n_v0 as i32).collect();
    for fi in 0..n_f {
        for k in 0..3 {
            let root = ds.find(fi * 3 + k);
            if root_to_vertex[root] != -1 { continue; }
            let v = f[fi][k] as usize;
            if !vertex_used[v] {
                vertex_used[v] = true;
                root_to_vertex[root] = v as i32;
            } else {
                root_to_vertex[root] = out.p.len() as i32;
                out.p.push(input.p[v]);
                origin.push(v as i32);
                rep.pinched_vertices += 1;
            }
        }
    }
    let mut g = vec![[0i32; 3]; n_f];
    for fi in 0..n_f {
        for k in 0..3 { g[fi][k] = root_to_vertex[ds.find(fi * 3 + k)]; }
    }

    // 5. faces that still share a directed edge (extremely rare) are dropped
    let mut alive = vec![true; n_f];
    for _ in 0..4 {
        let mut dir: Vec<(u64, usize)> = Vec::with_capacity(n_f * 3);
        for fi in 0..n_f {
            if !alive[fi] { continue; }
            for k in 0..3 {
                let a = g[fi][(k + 1) % 3] as u32 as u64;
                let b = g[fi][(k + 2) % 3] as u32 as u64;
                dir.push(((a << 32) | b, fi));
            }
        }
        dir.sort();
        let mut any = false;
        for i in 1..dir.len() {
            if dir[i].0 == dir[i - 1].0 && alive[dir[i].1] {
                alive[dir[i].1] = false;
                rep.collision_faces += 1;
                any = true;
            }
        }
        if !any { break; }
    }

    // 6. features: map original vertex pairs onto the repaired faces
    let feature_set: HashSet<u64> = input.features.iter().map(|e| edge_key(e[0], e[1])).collect();
    let mut feat_new: Vec<u64> = Vec::new();
    for fi in 0..n_f {
        if !alive[fi] { continue; }
        for k in 0..3 {
            let a = g[fi][(k + 1) % 3];
            let b = g[fi][(k + 2) % 3];
            if feature_set.contains(&edge_key(origin[a as usize], origin[b as usize])) { feat_new.push(edge_key(a, b)); }
        }
    }
    feat_new.sort_unstable();
    feat_new.dedup();

    // 7. compact: keep only used vertices
    let mut remap = vec![-1i32; out.p.len()];
    let mut next_id = 0i32;
    for fi in 0..n_f {
        if !alive[fi] { continue; }
        for k in 0..3 {
            let v = g[fi][k] as usize;
            if remap[v] == -1 { remap[v] = next_id; next_id += 1; }
        }
    }
    let mut p2 = vec![Vec3::ZERO; next_id as usize];
    let mut dens2 = if input.density.is_empty() { Vec::new() } else { vec![0f32; next_id as usize] };
    for v in 0..out.p.len() {
        if remap[v] != -1 {
            p2[remap[v] as usize] = out.p[v];
            if !dens2.is_empty() { dens2[remap[v] as usize] = input.density[origin[v] as usize]; }
        }
    }
    rep.unused_vertices = out.p.len() as i32 - next_id;
    out.p = p2;
    out.density = dens2;
    for fi in 0..n_f {
        if alive[fi] { out.f.push([remap[g[fi][0] as usize], remap[g[fi][1] as usize], remap[g[fi][2] as usize]]); }
    }
    for key in feat_new {
        let (a, b) = ((key >> 32) as usize, (key & 0xffff_ffff) as usize);
        if remap[a] != -1 && remap[b] != -1 { out.features.push([remap[a], remap[b]]); }
    }
    out
}
