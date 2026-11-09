//! Finishing of the extracted polygon mesh: split every polygon into quads, relax, remove dead ends, replace
//! "stars" (hole-filling fans with many spokes) by regular patches, and polish the worst quads.
//!
//! Work areas, in the order they run: `subdivide_polygons` · `relax` · `merge_dead_ends` · `replace_stars` ·
//! the quad-quality polish at the end of `finish`.
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::stages::quadopt::{optimize_poles, QuadOptStats};
use crate::stages::quadquality::{improve_quads, QuadQualityStats};
use std::collections::{HashMap, HashSet};

fn env_i(name: &str, default: i32) -> i32 { std::env::var(name).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(default) }
fn env_set(name: &str) -> bool { std::env::var_os(name).is_some() }
fn dbg() -> bool { env_set("FORGE_DBG") }

pub struct FinishParams {
    pub rho: f64,
    pub final_smooth: i32,
    /// Chord rotation moves per face (0 = off).
    pub pole_moves: f64,
}

/// All-quad mesh under construction. `feat[v]`: 0 free, 1 on a feature line, 2 corner.
struct Quads {
    p: Vec<Vec3>,
    f: Vec<[i32; 4]>,
    feat: Vec<u8>,
}

impl Quads {
    fn neighbours(&self) -> Vec<Vec<i32>> {
        let mut nb: Vec<Vec<i32>> = vec![Vec::new(); self.p.len()];
        for q in &self.f {
            for k in 0..4 {
                let (a, b) = (q[k], q[(k + 1) % 4]);
                if !nb[a as usize].contains(&b) { nb[a as usize].push(b); }
                if !nb[b as usize].contains(&a) { nb[b as usize].push(a); }
            }
        }
        nb
    }
    fn incident(&self) -> Vec<Vec<usize>> {
        let mut inc: Vec<Vec<usize>> = vec![Vec::new(); self.p.len()];
        for (q, f) in self.f.iter().enumerate() { for k in 0..4 { inc[f[k] as usize].push(q); } }
        inc
    }
    fn into_poly(self) -> PolyMesh {
        PolyMesh {
            p: self.p,
            sizes: vec![4; self.f.len()],
            idx: self.f.iter().flat_map(|q| q.iter().copied()).collect(),
            ..PolyMesh::default()
        }
    }
}

fn edge_key(a: i32, b: i32) -> u64 {
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    ((a as u32 as u64) << 32) | b as u32 as u64
}

fn rotate_to(mut q: [i32; 4], v: i32) -> [i32; 4] {
    while q[0] != v { q.rotate_left(1); }
    q
}

/// Every polygon becomes quads (n-gon -> n quads around its centre).
fn subdivide_polygons(poly: &PolyMesh, cfeat: &[u8], feat: &SegmentIndex, rho: f64) -> Quads {
    let mut s = Quads { p: poly.p.clone(), f: Vec::new(), feat: cfeat.to_vec() };
    let mut mid: HashMap<u64, i32> = HashMap::new();
    let mut at = 0usize;
    for &sz in &poly.sizes {
        let sz = sz as usize;
        let mut ctr = Vec3::ZERO;
        for k in 0..sz { ctr += poly.p[poly.idx[at + k] as usize]; }
        let cid = s.p.len() as i32;
        s.p.push(ctr / sz as f64);
        s.feat.push(0);
        for k in 0..sz {
            let (a, b, z) = (poly.idx[at + k], poly.idx[at + (k + 1) % sz], poly.idx[at + (k + sz - 1) % sz]);
            let mut midpoint = |s: &mut Quads, a: i32, b: i32| -> i32 {
                let key = edge_key(a, b);
                if let Some(&id) = mid.get(&key) { return id; }
                let id = s.p.len() as i32;
                let mp = (poly.p[a as usize] + poly.p[b as usize]) * 0.5;
                s.p.push(mp);
                let mut ff = 0u8;
                if s.feat[a as usize] >= 1 && s.feat[b as usize] >= 1 && !feat.is_empty() {
                    let h = feat.closest(mp);
                    if h.seg >= 0 && h.p.distance(mp) < 0.15 * rho { ff = 1; }
                }
                s.feat.push(ff);
                mid.insert(key, id);
                id
            };
            let mab = midpoint(&mut s, a, b);
            let mza = midpoint(&mut s, z, a);
            s.f.push([a, mab, cid, mza]);
        }
        at += sz;
    }
    s
}

/// Average the neighbours and snap back to the surface (or to the feature line).
fn relax(s: &mut Quads, nb: &[Vec<i32>], iters: i32, feat: &SegmentIndex, reference: &SurfaceIndex) {
    let nv = s.p.len();
    for _ in 0..iters {
        let mut np = s.p.clone();
        for v in 0..nv {
            if nb[v].is_empty() || s.feat[v] == 2 { continue; }
            let mut avg = Vec3::ZERO;
            let mut k = 0;
            for &w in &nb[v] {
                if s.feat[v] == 0 || s.feat[w as usize] >= 1 { avg += s.p[w as usize]; k += 1; }
            }
            if k == 0 { continue; }
            avg = avg / k as f64;
            let p = s.p[v] + (avg - s.p[v]) * 0.6;
            if s.feat[v] == 1 {
                let h = feat.closest(p);
                np[v] = if h.seg >= 0 { h.p } else { p };
                continue;
            }
            let h = reference.closest_any(p);
            np[v] = if h.tri >= 0 { h.p } else { p };
        }
        s.p = np;
    }
}


/// Thin strips: a row of quads whose two across-edges are (almost) zero length. This is what a chamfer or fillet
/// narrower than one cell turns into on the lattice (both of its feature lines land on the same lattice line).
/// A closed strip (a ring around a hole, a loop along a rim) is removed: its two sides are welded together. Strips
/// that are not closed loops are left alone, because welding them would leave triangles behind.
fn collapse_thin_strips(s: &mut Quads, ratio: f64) {
    if env_set("FORGE_NOSTRIP") { return; }
    for _attempt in 0..3 {
        let nq = s.f.len();
        // mean edge length
        let (mut sum, mut cnt) = (0.0, 0usize);
        for q in &s.f { for k in 0..4 { sum += s.p[q[k] as usize].distance(s.p[q[(k + 1) % 4] as usize]); cnt += 1; } }
        if cnt == 0 { return; }
        let thr = ratio * sum / cnt as f64;
        let short = |q: &[i32; 4], k: usize| s.p[q[k] as usize].distance(s.p[q[(k + 1) % 4] as usize]) < thr;
        // candidate strip quads: the two opposite short edges are `k` and `k + 2`
        let mut cand = vec![-1i32; nq]; // k of the first short edge (0 or 1), -1 if not a strip quad
        for (qi, q) in s.f.iter().enumerate() {
            let sh = [short(q, 0), short(q, 1), short(q, 2), short(q, 3)];
            if sh[0] && sh[2] && !sh[1] && !sh[3] { cand[qi] = 0; }
            else if sh[1] && sh[3] && !sh[0] && !sh[2] { cand[qi] = 1; }
        }
        // quads per edge
        let mut eq: HashMap<u64, Vec<usize>> = HashMap::new();
        for (qi, q) in s.f.iter().enumerate() { for k in 0..4 { eq.entry(edge_key(q[k], q[(k + 1) % 4])).or_default().push(qi); } }
        // chains of candidates linked through shared short edges
        let mut parent: Vec<usize> = (0..nq).collect();
        fn find(p: &mut Vec<usize>, mut x: usize) -> usize { while p[x] != x { p[x] = p[p[x]]; x = p[x]; } x }
        let short_edges = |qi: usize| -> [u64; 2] {
            let q = s.f[qi];
            let k = cand[qi] as usize;
            [edge_key(q[k], q[(k + 1) % 4]), edge_key(q[k + 2], q[(k + 3) % 4])]
        };
        for qi in 0..nq {
            if cand[qi] < 0 { continue; }
            for e in short_edges(qi) {
                for &o in &eq[&e] { if o != qi && cand[o] >= 0 { let (a, b) = (find(&mut parent, qi), find(&mut parent, o)); parent[a] = b; } }
            }
        }
        // a chain is a closed loop when every one of its short edges is shared by exactly two quads of the chain
        let mut ok_chain: HashMap<usize, bool> = HashMap::new();
        for qi in 0..nq {
            if cand[qi] < 0 { continue; }
            let r = find(&mut parent, qi);
            let entry = ok_chain.entry(r).or_insert(true);
            for e in short_edges(qi) {
                let list = &eq[&e];
                if list.len() != 2 || list.iter().any(|&o| cand[o] < 0) { *entry = false; }
            }
        }
        if dbg() {
            let mut len: HashMap<usize, usize> = HashMap::new();
            for qi in 0..nq { if cand[qi] >= 0 { *len.entry(find(&mut parent, qi)).or_insert(0) += 1; } }
            let closed = ok_chain.values().filter(|&&v| v).count();
            let mut open_len: Vec<usize> = ok_chain.iter().filter(|(_, &v)| !v).map(|(r, _)| len[r]).collect();
            open_len.sort_unstable();
            let mut reasons = [0usize; 6];
            for qi in 0..nq {
                if cand[qi] < 0 { continue; }
                let r = find(&mut parent, qi);
                if ok_chain[&r] { continue; }
                for e in short_edges(qi) {
                    let list = &eq[&e];
                    if list.len() != 2 { reasons[0] += 1; continue; }
                    for &o in list { if o != qi && cand[o] < 0 {
                        let ns = (0..4).filter(|&k| short(&s.f[o], k)).count();
                        reasons[1 + ns.min(4)] += 1;
                    } }
                }
            }
            eprintln!("open-chain edge neighbours: border/nonmanifold {} | neighbour with 0/1/2/3+ short edges {:?}", reasons[0], &reasons[1..]);
            eprintln!("strip chains: {} closed, {} open (lengths {:?})", closed, open_len.len(), &open_len[open_len.len().saturating_sub(12)..]);
        }
        if !ok_chain.values().any(|&v| v) { return; }
        let mut dsu: Vec<usize> = (0..s.p.len()).collect();
        let mut dead = vec![false; nq];
        let mut vert_chain: HashMap<usize, usize> = HashMap::new();
        let mut collapsed = 0;
        for qi in 0..nq {
            if cand[qi] < 0 { continue; }
            let r = find(&mut parent, qi);
            if !ok_chain[&r] { continue; }
            dead[qi] = true;
            collapsed += 1;
            let q = s.f[qi];
            let k = cand[qi] as usize;
            for (a, b) in [(q[k], q[(k + 1) % 4]), (q[k + 2], q[(k + 3) % 4])] {
                let (ra, rb) = (find(&mut dsu, a as usize), find(&mut dsu, b as usize));
                dsu[ra] = rb;
                vert_chain.insert(a as usize, r);
                vert_chain.insert(b as usize, r);
            }
        }
        // rebuild; a surviving quad that now has a repeated vertex means the chain cannot be welded
        let mut bad_chains: Vec<usize> = Vec::new();
        for (qi, q) in s.f.iter().enumerate() {
            if dead[qi] { continue; }
            let r: Vec<usize> = q.iter().map(|&v| find(&mut dsu, v as usize)).collect();
            let degenerate = (0..4).any(|i| (i + 1..4).any(|j| r[i] == r[j]));
            if degenerate { for &v in q { if let Some(&c) = vert_chain.get(&(v as usize)) { bad_chains.push(c); } } }
        }
        if !bad_chains.is_empty() {
            // forbid those chains and try again
            bad_chains.sort_unstable();
            bad_chains.dedup();
            for qi in 0..nq { if cand[qi] >= 0 { let r = find(&mut parent, qi); if bad_chains.contains(&r) { ok_chain.insert(r, false); } } }
            // recompute with the reduced set
            dsu = (0..s.p.len()).collect();
            dead = vec![false; nq];
            collapsed = 0;
            for qi in 0..nq {
                if cand[qi] < 0 { continue; }
                let r = find(&mut parent, qi);
                if !ok_chain[&r] { continue; }
                dead[qi] = true;
                collapsed += 1;
                let q = s.f[qi];
                let k = cand[qi] as usize;
                for (a, b) in [(q[k], q[(k + 1) % 4]), (q[k + 2], q[(k + 3) % 4])] {
                    let (ra, rb) = (find(&mut dsu, a as usize), find(&mut dsu, b as usize));
                    dsu[ra] = rb;
                }
            }
            let mut still_bad = false;
            for (qi, q) in s.f.iter().enumerate() {
                if dead[qi] { continue; }
                let r: Vec<usize> = q.iter().map(|&v| find(&mut dsu, v as usize)).collect();
                if (0..4).any(|i| (i + 1..4).any(|j| r[i] == r[j])) { still_bad = true; }
            }
            if still_bad || collapsed == 0 { return; }
        }
        // build the welded mesh
        let n = s.p.len();
        let mut acc = vec![Vec3::ZERO; n];
        let mut cntv = vec![0i32; n];
        let mut fmax = vec![0u8; n];
        for v in 0..n { let r = find(&mut dsu, v); acc[r] += s.p[v]; cntv[r] += 1; fmax[r] = fmax[r].max(s.feat[v]); }
        let mut rid = vec![-1i32; n];
        let mut np: Vec<Vec3> = Vec::new();
        let mut nf: Vec<u8> = Vec::new();
        let mut nq_list: Vec<[i32; 4]> = Vec::new();
        for (qi, q) in s.f.iter().enumerate() {
            if dead[qi] { continue; }
            let mut out = [0i32; 4];
            for k in 0..4 {
                let r = find(&mut dsu, q[k] as usize);
                if rid[r] < 0 { rid[r] = np.len() as i32; np.push(acc[r] / cntv[r] as f64); nf.push(fmax[r]); }
                out[k] = rid[r];
            }
            nq_list.push(out);
        }
        // the welded mesh must stay a clean manifold
        let mut ec: HashMap<u64, i32> = HashMap::new();
        let mut dir: HashSet<u64> = HashSet::new();
        let mut clean = true;
        for q in &nq_list {
            for k in 0..4 {
                let (a, b) = (q[k], q[(k + 1) % 4]);
                *ec.entry(edge_key(a, b)).or_insert(0) += 1;
                if !dir.insert(((a as u32 as u64) << 32) | b as u32 as u64) { clean = false; }
            }
        }
        if ec.values().any(|&c| c > 2) { clean = false; }
        if dbg() { eprintln!("thin strips: collapsed {collapsed} quads, clean {clean}"); }
        if !clean { return; }
        *s = Quads { p: np, f: nq_list, feat: nf };
        if collapsed == 0 { return; }
    }
}

/// Merge the two quads around a valence-2 (dead-end) vertex into one quad.
fn merge_dead_ends(s: &mut Quads) {
    let rounds = if env_set("FORGE_NOMERGE2") { 0 } else { env_i("FORGE_MROUNDS", 6) };
    for _ in 0..rounds {
        let nv = s.p.len();
        let inc = s.incident();
        let mut dead_q = vec![false; s.f.len()];
        let mut dead_v = vec![false; nv];
        let mut mod_q = vec![false; s.f.len()];
        let mut f4 = s.f.clone();
        let mut merged = 0;
        for v in 0..nv {
            if inc[v].len() != 2 || s.feat[v] >= 1 { continue; }
            let (qa, qb) = (inc[v][0], inc[v][1]);
            if dead_q[qa] || dead_q[qb] || mod_q[qa] || mod_q[qb] { continue; }
            let a = rotate_to(f4[qa], v as i32);
            let b = rotate_to(f4[qb], v as i32);
            // A = (v,a,b,c) and B = (v,c,d,a)
            if a[1] != b[3] || a[3] != b[1] { continue; }
            let c4 = [a[1], a[2], a[3], b[2]];
            let mut distinct = true;
            for i1 in 0..4 { for i2 in i1 + 1..4 { if c4[i1] == c4[i2] { distinct = false; } } }
            if !distinct { continue; }
            f4[qa] = c4;
            dead_q[qb] = true;
            dead_v[v] = true;
            mod_q[qa] = true;
            merged += 1;
        }
        if merged == 0 { break; }
        let mut remap = vec![-1i32; nv];
        let mut p = Vec::new();
        let mut feat = Vec::new();
        for v in 0..nv { if !dead_v[v] { remap[v] = p.len() as i32; p.push(s.p[v]); feat.push(s.feat[v]); } }
        let mut f = Vec::new();
        for (q, quad) in f4.iter().enumerate() {
            if dead_q[q] { continue; }
            f.push([remap[quad[0] as usize], remap[quad[1] as usize], remap[quad[2] as usize], remap[quad[3] as usize]]);
        }
        *s = Quads { p, f, feat };
        if dbg() { eprintln!("merged {merged} dead-end vertices"); }
    }
}

/// Stars: a vertex with many spokes (a fan that closed a hole) is replaced by a regular k x l patch of quads
/// whose corners are chosen where the neighbouring valences suffer least.
fn replace_stars(s: &mut Quads, reference: &SurfaceIndex) {
    if env_set("FORGE_NOSTAR") { return; }
    let nvs = s.p.len();
    let f4 = s.f.clone();
    let inc = s.incident();
    // boundary vertices of the quad mesh (an edge used once)
    let mut ecount: HashMap<u64, i32> = HashMap::new();
    for q in &f4 { for k in 0..4 { *ecount.entry(edge_key(q[k], q[(k + 1) & 3])).or_insert(0) += 1; } }
    let mut on_border = vec![false; nvs];
    for (k, &c) in &ecount { if c == 1 { on_border[(k >> 32) as usize] = true; on_border[(k & 0xffff_ffff) as usize] = true; } }
    let mut order: Vec<usize> = (0..nvs).collect();
    order.sort_by(|&a, &b| inc[b].len().cmp(&inc[a].len()));
    let mut dead_q = vec![false; f4.len()];
    let mut dead_v = vec![false; nvs];
    let mut touched = vec![false; nvs];
    let mut new_q: Vec<[i32; 4]> = Vec::new();
    let mut new_p: Vec<Vec3> = Vec::new();
    let mut replaced = 0;
    let star_min = env_i("FORGE_STARMIN", 6) as usize;
    let star_k = env_i("FORGE_STARK", 2) as usize;
    for &v in &order {
        let n = inc[v].len();
        if n < star_min { break; }
        if on_border[v] || s.feat[v] >= 1 || touched[v] { continue; }
        // quads around v in order: (v, a_i, b_i, a_{i+1})
        let mut st: Vec<[i32; 4]> = Vec::new();
        let mut ok = true;
        for &qi in &inc[v] {
            if dead_q[qi] { ok = false; }
            st.push(rotate_to(f4[qi], v as i32));
        }
        if !ok { continue; }
        let mut seq: Vec<[i32; 4]> = vec![st[0]];
        let mut used = vec![false; n];
        used[0] = true;
        for _ in 1..n {
            let want = seq.last().unwrap()[3];
            match (0..n).find(|&i| !used[i] && st[i][1] == want) {
                Some(found) => { used[found] = true; seq.push(st[found]); }
                None => { ok = false; break; }
            }
        }
        if !ok || seq.last().unwrap()[3] != seq[0][1] { continue; }
        let mut ring: Vec<i32> = Vec::new(); // a0 b0 a1 b1 ...
        for q in &seq { ring.push(q[1]); ring.push(q[2]); }
        let l = 2 * n;
        {
            let mut tmp = ring.clone();
            tmp.sort_unstable();
            if tmp.windows(2).any(|w| w[0] == w[1]) { continue; }
        }
        if ring.iter().any(|&u| on_border[u as usize] || touched[u as usize] || dead_v[u as usize]) { continue; }
        // outside degree of every ring vertex, and the cost of the choices
        let outd: Vec<i32> = (0..l).map(|j| inc[ring[j] as usize].len() as i32 - if j & 1 == 1 { 2 } else { 3 }).collect();
        let nn4 = ((n as f64) - 4.0) * ((n as f64) - 4.0);
        let mut old_cost = nn4;
        for &u in &ring { let d = inc[u as usize].len() as f64 - 4.0; old_cost += d * d; }
        let mut h = 0.0;
        for j in 0..l { h += s.p[ring[j] as usize].distance(s.p[ring[(j + 1) % l] as usize]); }
        h /= l as f64;
        let (mut best_cost, mut best_val, mut best_p, mut best_k) = (1e300f64, 1e300f64, 0usize, 0usize);
        for k in 1..n {
            let ll = n - k;
            if k.min(ll) < star_k { continue; }
            for p in 0..l {
                let c = [p, (p + k) % l, (p + k + ll) % l, (p + 2 * k + ll) % l];
                let mut cost = 0.0;
                for j in 0..l {
                    let corner = j == c[0] || j == c[1] || j == c[2] || j == c[3];
                    let val = outd[j] + if corner { 2 } else { 3 };
                    cost += ((val - 4) as f64) * ((val - 4) as f64);
                }
                // shape: the chord of every side should be about (cells on that side) x (edge length)
                let cnt = [k, ll, k, ll];
                let mut shape = 0.0;
                for s4 in 0..4 {
                    let ch = s.p[ring[c[s4]] as usize].distance(s.p[ring[c[(s4 + 1) & 3]] as usize]);
                    let r = ch / (cnt[s4] as f64 * h) - 1.0;
                    shape += r * r;
                }
                let total = cost + 8.0 * shape * n as f64 / 4.0;
                if total < best_cost { best_cost = total; best_val = cost; best_p = p; best_k = k; }
            }
        }
        let ring_before = old_cost - nn4;
        if !(best_val + 0.5 < old_cost) || best_val > ring_before + 0.25 * nn4 { continue; }
        let (k, ll, p) = (best_k, n - best_k, best_p);
        // boundary positions of the patch, bottom (i), right (j), top (i reversed), left (j reversed)
        let at = |off: usize| s.p[ring[(p + off) % l] as usize];
        let b: Vec<Vec3> = (0..=k).map(|i| at(i)).collect();
        let r: Vec<Vec3> = (0..=ll).map(|j| at(k + j)).collect();
        let t: Vec<Vec3> = (0..=k).map(|i| at(k + ll + (k - i))).collect();
        let lf: Vec<Vec3> = (0..=ll).map(|j| at(2 * k + ll + (ll - j))).collect();
        let mut grid = vec![vec![-1i32; ll + 1]; k + 1];
        for i in 0..=k {
            grid[i][0] = ring[(p + i) % l];
            grid[i][ll] = ring[(p + k + ll + (k - i)) % l];
        }
        for j in 0..=ll {
            grid[0][j] = ring[(p + 2 * k + ll + (ll - j)) % l];
            grid[k][j] = ring[(p + k + j) % l];
        }
        let base = nvs + new_p.len();
        let mut made = 0;
        for i in 1..k {
            for j in 1..ll {
                let sx = i as f64 / k as f64;
                let ty = j as f64 / ll as f64;
                let mut pos = lf[j] * (1.0 - sx) + r[j] * sx + b[i] * (1.0 - ty) + t[i] * ty
                    - (b[0] * ((1.0 - sx) * (1.0 - ty)) + b[k] * (sx * (1.0 - ty)) + t[0] * ((1.0 - sx) * ty) + t[k] * (sx * ty));
                let hit = reference.closest_any(pos);
                if hit.tri >= 0 { pos = hit.p; }
                grid[i][j] = (base + made) as i32;
                made += 1;
                new_p.push(pos);
            }
        }
        for i in 0..k { for j in 0..ll { new_q.push([grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]]); } }
        for &qi in &inc[v] { dead_q[qi] = true; }
        dead_v[v] = true;
        for &u in &ring { touched[u as usize] = true; }
        touched[v] = true;
        replaced += 1;
        if dbg() { eprintln!("star {n} spokes -> {k} x {ll} patch (cost {old_cost:.0} -> {best_val:.0})"); }
    }
    if replaced == 0 { return; }
    let mut remap = vec![-1i32; nvs + new_p.len()];
    let mut p = Vec::new();
    let mut feat = Vec::new();
    for v in 0..nvs { if !dead_v[v] { remap[v] = p.len() as i32; p.push(s.p[v]); feat.push(s.feat[v]); } }
    let first_new = p.len();
    for (i, np) in new_p.iter().enumerate() { remap[nvs + i] = p.len() as i32; p.push(*np); feat.push(0); }
    let mut f: Vec<[i32; 4]> = Vec::new();
    let emit = |q: &[i32; 4]| [remap[q[0] as usize], remap[q[1] as usize], remap[q[2] as usize], remap[q[3] as usize]];
    for (q, quad) in f4.iter().enumerate() { if !dead_q[q] { f.push(emit(quad)); } }
    for q in &new_q { f.push(emit(q)); }
    // smooth the new interior vertices (the rest stays)
    let mut nb_n: Vec<Vec<i32>> = vec![Vec::new(); p.len()];
    for q in &f { for k in 0..4 { let (a, b) = (q[k], q[(k + 1) % 4]); nb_n[a as usize].push(b); nb_n[b as usize].push(a); } }
    for _ in 0..200 {
        for v2 in first_new..p.len() {
            let mut avg = Vec3::ZERO;
            for &w in &nb_n[v2] { avg += p[w as usize]; }
            avg = avg / nb_n[v2].len() as f64;
            let pn = p[v2] + (avg - p[v2]) * 0.8;
            let h = reference.closest_any(pn);
            p[v2] = if h.tri >= 0 { h.p } else { pn };
        }
    }
    *s = Quads { p, f, feat };
    if dbg() { eprintln!("replaced {replaced} stars by patches"); }
}

/// Polygon mesh from the grid -> all-quad mesh. `cfeat[v]` is the feature class of each polygon vertex.
pub fn finish(poly: &PolyMesh, cfeat: &[u8], feat: &SegmentIndex, reference: &SurfaceIndex, prm: &FinishParams) -> PolyMesh {
    let mut s = subdivide_polygons(poly, cfeat, feat, prm.rho);
    // marked edges: every vertex close to a feature line joins it
    if !feat.is_empty() {
        let snap_dist = 0.3 * prm.rho;
        for v in 0..s.p.len() {
            let h = feat.closest(s.p[v]);
            if s.feat[v] == 2 { continue; }
            if h.seg >= 0 && h.p.distance(s.p[v]) < snap_dist { s.p[v] = h.p; s.feat[v] = 1; }
        }
    }
    let mut nb = s.neighbours();
    relax(&mut s, &nb, prm.final_smooth, feat, reference);
    if prm.pole_moves > 0.0 {
        let mut qo = QuadOptStats::default();
        optimize_poles(&s.p, &mut s.f, prm.pole_moves, 0.15, 7, &mut qo);
        if dbg() { eprintln!("pole opt: moves {} cost {} -> {}", qo.moves, qo.pole_cost_before, qo.pole_cost_after); }
        nb = s.neighbours();
        relax(&mut s, &nb, prm.final_smooth, feat, reference);
    }
    {
        let before = s.f.len();
        collapse_thin_strips(&mut s, env_i("FORGE_STRIPPCT", 25) as f64 / 100.0);
        if s.f.len() != before {
            nb = s.neighbours();
            relax(&mut s, &nb, prm.final_smooth / 2, feat, reference);
        }
    }
    merge_dead_ends(&mut s);
    replace_stars(&mut s, reference);
    if !env_set("FORGE_NOQQ") {
        let frozen: Vec<bool> = s.feat.iter().map(|&f| f >= 1).collect();
        let mut qqs = QuadQualityStats::default();
        improve_quads(&mut s.p, &s.f, &frozen, reference, 40, 0.6, &mut qqs);
        if dbg() {
            eprintln!("quad quality: worst {:.3} -> {:.3}, bad(<0.6) {} -> {}, moved {} in {} sweeps",
                qqs.worst_before, qqs.worst_after, qqs.bad_before, qqs.bad_after, qqs.moved, qqs.sweeps);
        }
    }
    s.into_poly()
}
