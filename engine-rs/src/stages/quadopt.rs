//! Pole optimisation on a pure quad mesh.
//!
//! One move only: the "chord rotation". Two quads that share an edge (a,b) form a hexagon; the shared edge can
//! be replaced by either of the two other chords that split the hexagon into two quads. The move lowers the
//! valence of a and b by one and raises the valence of the two new chord ends. Simulated annealing over these
//! moves lets poles travel and cancel (a 3 next to a 5 disappears after a few moves) while a geometric term
//! keeps the quads square.
use crate::math::Vec3;
use crate::util::rng::Mt19937;
use std::collections::HashMap;

#[derive(Clone, Debug, Default)]
pub struct QuadOptStats {
    pub moves: i32,
    pub pole_cost_before: i32,
    pub pole_cost_after: i32,
}

fn key(a: i32, b: i32) -> u64 { ((a as u32 as u64) << 32) | b as u32 as u64 }

pub fn optimize_poles(p: &[Vec3], f: &mut [[i32; 4]], moves_per_face: f64, geom_weight: f64, seed: u32, st: &mut QuadOptStats) {
    let n_v = p.len();
    let n_f = f.len();
    if n_f < 2 { return; }
    let mut he: HashMap<u64, i32> = HashMap::with_capacity(n_f * 8);
    for fi in 0..n_f { for k in 0..4 { he.insert(key(f[fi][k], f[fi][(k + 1) % 4]), fi as i32); } }
    let mut nbr: Vec<Vec<i32>> = vec![Vec::new(); n_v];
    let mut bnd = vec![false; n_v];
    let add_n = |nbr: &mut Vec<Vec<i32>>, a: i32, b: i32| { if !nbr[a as usize].contains(&b) { nbr[a as usize].push(b); } };
    for fi in 0..n_f {
        for k in 0..4 {
            let (a, b) = (f[fi][k], f[fi][(k + 1) % 4]);
            add_n(&mut nbr, a, b);
            add_n(&mut nbr, b, a);
            if !he.contains_key(&key(b, a)) { bnd[a as usize] = true; bnd[b as usize] = true; }
        }
    }
    let cost = |bnd: &[bool], v: i32, val: i32| if bnd[v as usize] { 0 } else { (val - 4).abs() };
    for v in 0..n_v { st.pole_cost_before += cost(&bnd, v as i32, nbr[v].len() as i32); }

    let corner_term = |p0: Vec3, pm: Vec3, p1: Vec3, n: Vec3| {
        let (u, w) = (p0 - pm, p1 - pm);
        let (lu, lw) = (u.length(), w.length());
        if lu < 1e-12 || lw < 1e-12 { return 10.0; }
        let c = u.dot(w) / (lu * lw);
        let mut t = c * c;
        let s = w.cross(u).dot(n); // orientation of the corner, positive when convex
        if s <= 0.0 { t += 4.0 + 4.0 * (-s / (lu * lw)); }
        t
    };
    let geom = |q: &[i32; 4], n: Vec3| {
        let mut e = 0.0;
        for k in 0..4 {
            e += corner_term(p[q[(k + 3) % 4] as usize], p[q[k] as usize], p[q[(k + 1) % 4] as usize], n);
        }
        e
    };
    let face_normal = |q: &[i32; 4]| (p[q[2] as usize] - p[q[0] as usize]).cross(p[q[3] as usize] - p[q[1] as usize]);

    let mut rng = Mt19937::new(seed);
    let total = (moves_per_face * n_f as f64) as i64;
    for it in 0..total {
        let t = 0.6 * (0.03f64 / 0.6).powf(it as f64 / total.max(1) as f64);
        let fi = (rng.next_u32() as usize) % n_f;
        let k = (rng.next_u32() & 3) as usize;
        let (a, b) = (f[fi][k], f[fi][(k + 1) % 4]);
        if bnd[a as usize] || bnd[b as usize] { continue; }
        let g = match he.get(&key(b, a)) { Some(&g) => g as usize, None => continue };
        let (pp, q) = (f[fi][(k + 2) % 4], f[fi][(k + 3) % 4]);
        let mut j = 0;
        while j < 4 && !(f[g][j] == b && f[g][(j + 1) % 4] == a) { j += 1; }
        if j == 4 { continue; }
        let (r, s) = (f[g][(j + 2) % 4], f[g][(j + 3) % 4]);
        let variant = (rng.next_u32() & 1) as i32;
        let (c1, c2) = if variant != 0 { (s, q) } else { (r, pp) };
        let six = [a, b, pp, q, r, s];
        let mut distinct = true;
        'outer: for x in 0..6 { for y in x + 1..6 { if six[x] == six[y] { distinct = false; break 'outer; } } }
        if !distinct { continue; }
        if bnd[c1 as usize] || bnd[c2 as usize] { continue; }
        if nbr[c1 as usize].contains(&c2) { continue; }
        let va = nbr[a as usize].len() as i32 - 1;
        let vb = nbr[b as usize].len() as i32 - 1;
        let v1 = nbr[c1 as usize].len() as i32 + 1;
        let v2 = nbr[c2 as usize].len() as i32 + 1;
        if va < 3 || vb < 3 || v1 > 7 || v2 > 7 { continue; }
        let d_pole = ((cost(&bnd, a, va) + cost(&bnd, b, vb) + cost(&bnd, c1, v1) + cost(&bnd, c2, v2))
            - (cost(&bnd, a, va + 1) + cost(&bnd, b, vb + 1) + cost(&bnd, c1, v1 - 1) + cost(&bnd, c2, v2 - 1))) as f64;
        let (n1, n2) = if variant == 0 { ([r, s, b, pp], [pp, q, a, r]) } else { ([s, b, pp, q], [q, a, r, s]) };
        let mut nref = face_normal(&f[fi]) + face_normal(&f[g]);
        let ln = nref.length();
        if ln < 1e-20 { continue; }
        nref = nref / ln;
        let d_geom = geom(&n1, nref) + geom(&n2, nref) - geom(&f[fi], nref) - geom(&f[g], nref);
        let d_e = d_pole + geom_weight * d_geom;
        if d_e > 0.0 && rng.next_f64() > (-d_e / t).exp() { continue; }
        // apply
        for x in 0..4 {
            he.remove(&key(f[fi][x], f[fi][(x + 1) % 4]));
            he.remove(&key(f[g][x], f[g][(x + 1) % 4]));
        }
        f[fi] = n1;
        f[g] = n2;
        for x in 0..4 {
            he.insert(key(f[fi][x], f[fi][(x + 1) % 4]), fi as i32);
            he.insert(key(f[g][x], f[g][(x + 1) % 4]), g as i32);
        }
        let ia = nbr[a as usize].iter().position(|&x| x == b).unwrap();
        nbr[a as usize].remove(ia);
        let ib = nbr[b as usize].iter().position(|&x| x == a).unwrap();
        nbr[b as usize].remove(ib);
        nbr[c1 as usize].push(c2);
        nbr[c2 as usize].push(c1);
        st.moves += 1;
    }
    for v in 0..n_v { st.pole_cost_after += cost(&bnd, v as i32, nbr[v].len() as i32); }
}
