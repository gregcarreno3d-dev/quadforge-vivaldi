//! Sparse matrices and a smoothed-aggregation algebraic multigrid preconditioner.
//!
//! Used by `param`: the parametrization system is a (very large, badly conditioned) Laplacian-like matrix over
//! blocks of two unknowns (u, v). Plain conjugate gradient needs thousands of iterations on it; one V-cycle per
//! iteration brings that down to tens.
//!
//! Work areas: `Csr::build` (assembly) · `Amg::aggregate` (coarsening rule, strength threshold) ·
//! `Amg::vcycle` (smoother and cycle shape) · dense Cholesky of the coarsest level in `Amg::build`.
use std::cmp::Ordering;

#[derive(Clone, Default)]
pub struct Csr {
    pub n: usize,
    pub start: Vec<i32>,
    pub col: Vec<i32>,
    pub val: Vec<f64>,
}

impl Csr {
    /// Triplets (row, col, value) with duplicates summed; rows/cols are f64 only to keep call sites short.
    pub fn build(&mut self, size: usize, trip: &mut Vec<[f64; 3]>) {
        self.n = size;
        trip.sort_by(|a, b| {
            if a[0] != b[0] { a[0].partial_cmp(&b[0]).unwrap_or(Ordering::Equal) } else { a[1].partial_cmp(&b[1]).unwrap_or(Ordering::Equal) }
        });
        self.start = vec![0; size + 1];
        self.col.clear();
        self.val.clear();
        let mut i = 0;
        while i < trip.len() {
            let mut j = i;
            let mut s = 0.0;
            while j < trip.len() && trip[j][0] == trip[i][0] && trip[j][1] == trip[i][1] { s += trip[j][2]; j += 1; }
            self.col.push(trip[i][1] as i32);
            self.val.push(s);
            self.start[trip[i][0] as usize + 1] += 1;
            i = j;
        }
        for r in 0..size { self.start[r + 1] += self.start[r]; }
    }

    pub fn mul(&self, x: &[f64], y: &mut [f64]) {
        for r in 0..self.n {
            let mut s = 0.0;
            for k in self.start[r] as usize..self.start[r + 1] as usize { s += self.val[k] * x[self.col[k] as usize]; }
            y[r] = s;
        }
    }
}

/// Rectangular sparse matrix (rows x cols), CSR.
#[derive(Clone, Default)]
struct Rect {
    rows: usize,
    cols: usize,
    start: Vec<i32>,
    col: Vec<i32>,
    val: Vec<f64>,
}

impl Rect {
    fn from_csr(c: &Csr) -> Rect { Rect { rows: c.n, cols: c.n, start: c.start.clone(), col: c.col.clone(), val: c.val.clone() } }

    fn transpose(&self) -> Rect {
        let mut t = Rect { rows: self.cols, cols: self.rows, ..Rect::default() };
        t.start = vec![0; t.rows + 1];
        for &c in &self.col { t.start[c as usize + 1] += 1; }
        for r in 0..t.rows { t.start[r + 1] += t.start[r]; }
        t.col = vec![0; self.col.len()];
        t.val = vec![0.0; self.val.len()];
        let mut fill: Vec<i32> = t.start[..t.rows].to_vec();
        for r in 0..self.rows {
            for k in self.start[r] as usize..self.start[r + 1] as usize {
                let c = self.col[k] as usize;
                let p = fill[c] as usize;
                fill[c] += 1;
                t.col[p] = r as i32;
                t.val[p] = self.val[k];
            }
        }
        t
    }

    /// C = A * B
    fn multiply(a: &Rect, b: &Rect) -> Rect {
        let mut c = Rect { rows: a.rows, cols: b.cols, ..Rect::default() };
        c.start = vec![0; c.rows + 1];
        let mut mark = vec![-1i64; b.cols];
        let mut acc = vec![0.0f64; b.cols];
        let mut cols: Vec<i32> = Vec::new();
        for r in 0..a.rows {
            cols.clear();
            for k in a.start[r] as usize..a.start[r + 1] as usize {
                let m = a.col[k] as usize;
                let av = a.val[k];
                for q in b.start[m] as usize..b.start[m + 1] as usize {
                    let j = b.col[q] as usize;
                    if mark[j] != r as i64 { mark[j] = r as i64; acc[j] = 0.0; cols.push(j as i32); }
                    acc[j] += av * b.val[q];
                }
            }
            cols.sort_unstable();
            for &j in &cols { c.col.push(j); c.val.push(acc[j as usize]); }
            c.start[r + 1] = c.col.len() as i32;
        }
        c
    }

    fn mul_vec(&self, x: &[f64], y: &mut Vec<f64>) {
        y.clear();
        y.resize(self.rows, 0.0);
        for r in 0..self.rows {
            let mut s = 0.0;
            for k in self.start[r] as usize..self.start[r + 1] as usize { s += self.val[k] * x[self.col[k] as usize]; }
            y[r] = s;
        }
    }
}

#[derive(Default)]
struct Level {
    a: Rect,
    g: Rect,
    p: Rect,
    r: Rect,
    diag: Vec<f64>,
    gdiag: Vec<f64>,
}

#[derive(Default)]
pub struct Amg {
    levels: Vec<Level>,
    dense: Vec<f64>,
    dens_n: usize,
    bs: usize,
}

fn finish_level(l: &mut Level) {
    let n = l.a.rows;
    l.diag = vec![1.0; n];
    for r in 0..n {
        for k in l.a.start[r] as usize..l.a.start[r + 1] as usize {
            if l.a.col[k] as usize == r { l.diag[r] = l.a.val[k].max(1e-300); }
        }
    }
    l.gdiag = vec![1.0; n];
    for r in 0..n {
        for k in l.g.start[r] as usize..l.g.start[r + 1] as usize {
            if l.g.col[k] as usize == r { l.gdiag[r] = l.g.val[k].max(1e-300); }
        }
    }
}

impl Amg {
    pub fn new() -> Self { Amg { bs: 2, ..Amg::default() } }

    /// `k` must be symmetric positive definite (add a small shift beforehand when it is only semi-definite).
    /// Unknowns come in blocks of `bs` (2 here); aggregates are made over blocks.
    /// `g`: the part of `k` that couples unknowns in the same frame (used for aggregation and smoothing).
    pub fn build(&mut self, k: &Csr, g: &Csr, bs: usize, max_levels: usize, coarse_size: usize) {
        self.levels.clear();
        self.bs = bs;
        self.levels.push(Level { a: Rect::from_csr(k), g: Rect::from_csr(g), ..Level::default() });
        let mut l = 0;
        while l + 1 < max_levels {
            let cur = self.levels.last_mut().unwrap();
            finish_level(cur);
            if cur.a.rows <= coarse_size { break; }
            let p = match Self::aggregate(cur, bs) { Some(p) => p, None => break };
            let pt = p.transpose();
            let ap = Rect::multiply(&cur.a, &p);
            let nxt = Level { a: Rect::multiply(&pt, &ap), g: Rect::multiply(&pt, &Rect::multiply(&cur.g, &p)), ..Level::default() };
            cur.p = p;
            cur.r = pt;
            let stop = nxt.a.rows >= cur.a.rows * 9 / 10;
            self.levels.push(nxt);
            if stop { break; }
            l += 1;
        }
        finish_level(self.levels.last_mut().unwrap());
        // dense Cholesky of the coarsest matrix
        let c = self.levels.last().unwrap();
        let n = c.a.rows;
        let mut d = vec![0.0f64; n * n];
        for r in 0..n {
            for k in c.a.start[r] as usize..c.a.start[r + 1] as usize { d[r * n + c.a.col[k] as usize] = c.a.val[k]; }
        }
        for r in 0..n { d[r * n + r] *= 1.0 + 1e-10; }
        for j in 0..n {
            let mut dj = d[j * n + j];
            for k in 0..j { dj -= d[j * n + k] * d[j * n + k]; }
            if dj < 1e-300 { dj = 1e-300; }
            dj = dj.sqrt();
            d[j * n + j] = dj;
            for i in j + 1..n {
                let mut s = d[i * n + j];
                for k in 0..j { s -= d[i * n + k] * d[j * n + k]; }
                d[i * n + j] = s / dj;
            }
        }
        self.dense = d;
        self.dens_n = n;
    }

    pub fn num_levels(&self) -> usize { self.levels.len() }
    pub fn coarse_rows(&self) -> usize { self.levels.last().map_or(0, |l| l.a.rows) }

    /// z = approximately K^{-1} r (one V-cycle, symmetric).
    pub fn apply(&self, r: &[f64], z: &mut Vec<f64>) {
        z.clear();
        z.resize(r.len(), 0.0);
        self.vcycle(0, r, z);
    }

    /// Smoothed aggregation: the tentative prolongator is block-constant per aggregate, then one damped Jacobi step.
    fn aggregate(l: &Level, bs: usize) -> Option<Rect> {
        let n = l.g.rows;
        let nb = n / bs;
        let mut agg = vec![-1i32; nb];
        let g = &l.g;
        // coupling of block bi to bj relative to the diagonals
        let strong = |bi: usize, bj: usize, thr: f64| -> bool {
            let mut s = 0.0f64;
            for a in 0..bs {
                let r = bi * bs + a;
                for k in g.start[r] as usize..g.start[r + 1] as usize {
                    let c = g.col[k] as usize;
                    if c / bs == bj { s = s.max(g.val[k].abs() / (l.gdiag[r] * l.gdiag[c]).sqrt()); }
                }
            }
            s > thr
        };
        let thr = 0.08;
        let mut na = 0i32;
        let mut nbrs: Vec<usize> = Vec::new();
        for bi in 0..nb {
            if agg[bi] >= 0 { continue; }
            // only seed an aggregate when all strong neighbours are free
            nbrs.clear();
            let mut free = true;
            let mut a = 0;
            while a < bs && free {
                let r = bi * bs + a;
                a += 1;
                for k in g.start[r] as usize..g.start[r + 1] as usize {
                    let bj = g.col[k] as usize / bs;
                    if bj == bi || nbrs.contains(&bj) || !strong(bi, bj, thr) { continue; }
                    nbrs.push(bj);
                    if agg[bj] >= 0 { free = false; break; }
                }
            }
            if !free { continue; }
            agg[bi] = na;
            for &bj in &nbrs { agg[bj] = na; }
            na += 1;
        }
        // leftovers join the aggregate of a strong neighbour (or form their own)
        for bi in 0..nb {
            if agg[bi] >= 0 { continue; }
            let mut best = -1i32;
            let mut best_s = 0.0f64;
            for a in 0..bs {
                let r = bi * bs + a;
                for k in g.start[r] as usize..g.start[r + 1] as usize {
                    let c = g.col[k] as usize;
                    let bj = c / bs;
                    if bj == bi || agg[bj] < 0 { continue; }
                    let s = g.val[k].abs() / (l.gdiag[r] * l.gdiag[c]).sqrt();
                    if s > best_s { best_s = s; best = agg[bj]; }
                }
            }
            if best >= 0 { agg[bi] = best; } else { agg[bi] = na; na += 1; }
        }
        let na = na as usize;
        if na >= nb { return None; }
        // tentative prolongator
        let t = Rect {
            rows: n,
            cols: na * bs,
            start: (0..=n as i32).collect(),
            col: (0..n).map(|r| (agg[r / bs] as usize * bs + r % bs) as i32).collect(),
            val: vec![1.0; n],
        };
        // P = (I - w D^-1 A) T
        let w = 2.0 / 3.0;
        let mut s = Rect { rows: n, cols: n, ..Rect::default() };
        s.start = vec![0; n + 1];
        for r in 0..n {
            for k in g.start[r] as usize..g.start[r + 1] as usize {
                let c = g.col[k] as usize;
                let mut v = -w * g.val[k] / l.gdiag[r];
                if c == r { v += 1.0; }
                s.col.push(c as i32);
                s.val.push(v);
            }
            s.start[r + 1] = s.col.len() as i32;
        }
        Some(Rect::multiply(&s, &t))
    }

    fn gs_forward(a: &Rect, d: &[f64], b: &[f64], x: &mut [f64]) {
        for r in 0..a.rows {
            let mut s = b[r];
            for k in a.start[r] as usize..a.start[r + 1] as usize {
                let c = a.col[k] as usize;
                if c != r { s -= a.val[k] * x[c]; }
            }
            x[r] = s / d[r];
        }
    }

    fn gs_backward(a: &Rect, d: &[f64], b: &[f64], x: &mut [f64]) {
        for r in (0..a.rows).rev() {
            let mut s = b[r];
            for k in a.start[r] as usize..a.start[r + 1] as usize {
                let c = a.col[k] as usize;
                if c != r { s -= a.val[k] * x[c]; }
            }
            x[r] = s / d[r];
        }
    }

    fn vcycle(&self, li: usize, b: &[f64], x: &mut Vec<f64>) {
        let l = &self.levels[li];
        if li + 1 == self.levels.len() {
            let n = self.dens_n;
            let d = &self.dense;
            let mut y = b.to_vec();
            for i in 0..n {
                let mut s = y[i];
                for k in 0..i { s -= d[i * n + k] * y[k]; }
                y[i] = s / d[i * n + i];
            }
            for i in (0..n).rev() {
                let mut s = y[i];
                for k in i + 1..n { s -= d[k * n + i] * y[k]; }
                y[i] = s / d[i * n + i];
            }
            *x = y;
            return;
        }
        Self::gs_forward(&l.a, &l.diag, b, x); // pre-smoothing (x starts at zero)
        let mut res = Vec::new();
        l.a.mul_vec(x, &mut res);
        for i in 0..res.len() { res[i] = b[i] - res[i]; }
        let mut rc = Vec::new();
        l.r.mul_vec(&res, &mut rc);
        let mut xc = vec![0.0f64; self.levels[li + 1].a.rows];
        self.vcycle(li + 1, &rc, &mut xc);
        let mut corr = Vec::new();
        l.p.mul_vec(&xc, &mut corr);
        for i in 0..x.len() { x[i] += corr[i]; }
        Self::gs_backward(&l.a, &l.diag, b, x); // post-smoothing
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 2-unknown blocks on a 60x60 grid Laplacian (+ small shift): PCG with one V-cycle must converge fast.
    #[test]
    fn pcg_with_vcycle_converges() {
        let m = 60usize;
        let nb = m * m;
        let n = nb * 2;
        let mut trip: Vec<[f64; 3]> = Vec::new();
        for y in 0..m {
            for x in 0..m {
                let b = y * m + x;
                for c in 0..2 {
                    let r = (2 * b + c) as f64;
                    let mut deg = 1e-3;
                    for (dx, dy) in [(1i32, 0i32), (-1, 0), (0, 1), (0, -1)] {
                        let (nx, ny) = (x as i32 + dx, y as i32 + dy);
                        if nx < 0 || ny < 0 || nx >= m as i32 || ny >= m as i32 { continue; }
                        deg += 1.0;
                        trip.push([r, (2 * (ny as usize * m + nx as usize) + c) as f64, -1.0]);
                    }
                    trip.push([r, r, deg]);
                }
            }
        }
        let mut k = Csr::default();
        k.build(n, &mut trip);
        let mut amg = Amg::new();
        amg.build(&k, &k, 2, 12, 300);
        assert!(amg.num_levels() >= 2);
        let b: Vec<f64> = (0..n).map(|i| ((i * 7919) % 13) as f64 - 6.0).collect();
        let mut x = vec![0.0; n];
        let mut r = b.clone();
        let mut z = Vec::new();
        amg.apply(&r, &mut z);
        let mut p = z.clone();
        let mut rz: f64 = r.iter().zip(&z).map(|(a, b)| a * b).sum();
        let bn: f64 = b.iter().map(|v| v * v).sum::<f64>().sqrt();
        let mut ap = vec![0.0; n];
        let mut it = 0;
        while it < 100 {
            k.mul(&p, &mut ap);
            let alpha = rz / p.iter().zip(&ap).map(|(a, b)| a * b).sum::<f64>();
            for i in 0..n { x[i] += alpha * p[i]; r[i] -= alpha * ap[i]; }
            let rn: f64 = r.iter().map(|v| v * v).sum::<f64>().sqrt();
            it += 1;
            if rn < 1e-8 * bn { break; }
            amg.apply(&r, &mut z);
            let rz2: f64 = r.iter().zip(&z).map(|(a, b)| a * b).sum();
            let beta = rz2 / rz;
            rz = rz2;
            for i in 0..n { p[i] = z[i] + beta * p[i]; }
        }
        eprintln!("levels {} coarse {} iterations {}", amg.num_levels(), amg.coarse_rows(), it);
        assert!(it < 40, "too many iterations: {it}");
    }
}
