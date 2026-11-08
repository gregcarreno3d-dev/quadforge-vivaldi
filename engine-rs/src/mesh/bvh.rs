//! Bounding volume hierarchy for closest-point queries, plus the surface and polyline indexes built on it.
use crate::math::{geom, Aabb, Vec3, INF};

const LEAF: usize = 4;

#[derive(Clone, Default)]
struct Node {
    bbox: Aabb,
    left: i32,
    right: i32,
    first: usize,
    count: usize,
}

/// Generic BVH over primitives given as boxes. The caller supplies an evaluator that computes the squared
/// distance to one primitive and records the hit itself.
#[derive(Default)]
pub struct Bvh {
    nodes: Vec<Node>,
    order: Vec<usize>,
}

impl Bvh {
    pub fn build(&mut self, boxes: &[Aabb]) {
        let n = boxes.len();
        self.order = (0..n).collect();
        self.nodes.clear();
        if n == 0 { return; }
        let centers: Vec<Vec3> = boxes.iter().map(|b| b.center()).collect();
        self.nodes.push(Node { left: -1, right: -1, ..Default::default() });
        let mut stack = vec![(0usize, 0usize, n)];
        while let Some((node, begin, end)) = stack.pop() {
            let mut bbox = Aabb::default();
            let mut cbox = Aabb::default();
            for i in begin..end {
                bbox.expand(&boxes[self.order[i]]);
                cbox.expand_point(centers[self.order[i]]);
            }
            self.nodes[node].bbox = bbox;
            let count = end - begin;
            let ext = cbox.extent();
            let mut axis = 0;
            if ext.y > ext.x { axis = 1; }
            if ext.z > ext[axis] { axis = 2; }
            if count <= LEAF || ext[axis] <= 0.0 {
                self.nodes[node].first = begin;
                self.nodes[node].count = count;
                continue;
            }
            let mid = begin + count / 2;
            self.order[begin..end].select_nth_unstable_by(mid - begin, |&a, &b| {
                centers[a][axis].partial_cmp(&centers[b][axis]).unwrap_or(std::cmp::Ordering::Equal)
            });
            let l = self.nodes.len() as i32;
            self.nodes.push(Node { left: -1, right: -1, ..Default::default() });
            self.nodes.push(Node { left: -1, right: -1, ..Default::default() });
            self.nodes[node].left = l;
            self.nodes[node].right = l + 1;
            stack.push((l as usize, begin, mid));
            stack.push((l as usize + 1, mid, end));
        }
    }

    pub fn is_empty(&self) -> bool { self.nodes.is_empty() }

    /// `eval(primitive, best_d2)` returns the squared distance if it is smaller than `best_d2` (recording the
    /// hit itself), otherwise anything `>= best_d2`.
    pub fn closest<F: FnMut(usize, f64) -> f64>(&self, p: Vec3, best2: &mut f64, mut eval: F) {
        if self.nodes.is_empty() { return; }
        let mut stack = [0usize; 96];
        let mut sd = [0f64; 96];
        let mut sp = 0usize;
        stack[sp] = 0;
        sd[sp] = self.nodes[0].bbox.distance2_to(p);
        sp += 1;
        while sp > 0 {
            sp -= 1;
            if sd[sp] >= *best2 { continue; }
            let nd = &self.nodes[stack[sp]];
            if nd.count > 0 {
                for i in nd.first..nd.first + nd.count {
                    let d2 = eval(self.order[i], *best2);
                    if d2 < *best2 { *best2 = d2; }
                }
            } else {
                let (l, r) = (nd.left as usize, nd.right as usize);
                let dl = self.nodes[l].bbox.distance2_to(p);
                let dr = self.nodes[r].bbox.distance2_to(p);
                // push the farther child first so the nearer one is processed first
                if dl < dr {
                    if dr < *best2 { stack[sp] = r; sd[sp] = dr; sp += 1; }
                    if dl < *best2 { stack[sp] = l; sd[sp] = dl; sp += 1; }
                } else {
                    if dl < *best2 { stack[sp] = l; sd[sp] = dl; sp += 1; }
                    if dr < *best2 { stack[sp] = r; sd[sp] = dr; sp += 1; }
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct SurfaceHit {
    pub d2: f64,
    pub tri: i32,
    /// Barycentric weights of vertices b and c.
    pub u: f64,
    pub v: f64,
    pub p: Vec3,
}

impl Default for SurfaceHit {
    fn default() -> Self { SurfaceHit { d2: INF, tri: -1, u: 0.0, v: 0.0, p: Vec3::ZERO } }
}

/// Triangle surface with closest-point queries (the reference the remesh snaps onto).
#[derive(Default)]
pub struct SurfaceIndex {
    p: Vec<Vec3>,
    t: Vec<[i32; 3]>,
    normals: Vec<Vec3>,
    bvh: Bvh,
}

impl SurfaceIndex {
    pub fn new(p: &[Vec3], t: &[[i32; 3]]) -> Self {
        let mut s = SurfaceIndex { p: p.to_vec(), t: t.to_vec(), ..Default::default() };
        let mut boxes = Vec::with_capacity(t.len());
        for tri in t {
            let (a, b, c) = (p[tri[0] as usize], p[tri[1] as usize], p[tri[2] as usize]);
            let mut bx = Aabb::default();
            bx.expand_point(a);
            bx.expand_point(b);
            bx.expand_point(c);
            boxes.push(bx);
            s.normals.push(geom::triangle_normal_unnormalized(a, b, c).normalized());
        }
        s.bvh.build(&boxes);
        s
    }

    /// Closest point on the surface. With `normal`, triangles facing away from it (dot < `min_dot`) are skipped,
    /// which stops thin shells from grabbing points that belong to the other side.
    pub fn closest(&self, q: Vec3, normal: Option<Vec3>, min_dot: f64) -> SurfaceHit {
        let hit = self.query(q, normal, min_dot);
        if hit.tri < 0 && normal.is_some() { return self.query(q, None, -2.0); }
        hit
    }

    pub fn closest_any(&self, q: Vec3) -> SurfaceHit { self.closest(q, None, -2.0) }

    pub fn face_normal(&self, tri: i32) -> Vec3 { self.normals[tri as usize] }
    pub fn is_empty(&self) -> bool { self.bvh.is_empty() }

    fn query(&self, q: Vec3, normal: Option<Vec3>, min_dot: f64) -> SurfaceHit {
        let mut best = SurfaceHit::default();
        let mut best2 = INF;
        self.bvh.closest(q, &mut best2, |i, bound| {
            if let Some(n) = normal {
                if self.normals[i].dot(n) < min_dot { return INF; }
            }
            let t = self.t[i];
            let (c, u, v) = geom::closest_point_on_triangle(q, self.p[t[0] as usize], self.p[t[1] as usize], self.p[t[2] as usize]);
            let d2 = c.distance2(q);
            if d2 < bound { best = SurfaceHit { d2, tri: i as i32, u, v, p: c }; }
            d2
        });
        best
    }
}

#[derive(Clone, Copy, Debug)]
pub struct SegmentHit {
    pub d2: f64,
    pub seg: i32,
    pub t: f64,
    pub p: Vec3,
}

impl Default for SegmentHit {
    fn default() -> Self { SegmentHit { d2: INF, seg: -1, t: 0.0, p: Vec3::ZERO } }
}

/// Polylines (feature edges, open borders) with closest-point queries.
#[derive(Default)]
pub struct SegmentIndex {
    s: Vec<[Vec3; 2]>,
    bvh: Bvh,
}

impl SegmentIndex {
    pub fn new(segments: Vec<[Vec3; 2]>) -> Self {
        let mut boxes = Vec::with_capacity(segments.len());
        for s in &segments {
            let mut b = Aabb::default();
            b.expand_point(s[0]);
            b.expand_point(s[1]);
            boxes.push(b);
        }
        let mut idx = SegmentIndex { s: segments, bvh: Bvh::default() };
        idx.bvh.build(&boxes);
        idx
    }

    pub fn closest(&self, q: Vec3) -> SegmentHit {
        let mut best = SegmentHit::default();
        let mut best2 = INF;
        self.bvh.closest(q, &mut best2, |i, bound| {
            let (c, t) = geom::closest_point_on_segment(q, self.s[i][0], self.s[i][1]);
            let d2 = c.distance2(q);
            if d2 < bound { best = SegmentHit { d2, seg: i as i32, t, p: c }; }
            d2
        });
        best
    }

    pub fn is_empty(&self) -> bool { self.s.is_empty() }
    pub fn segment(&self, i: i32) -> &[Vec3; 2] { &self.s[i as usize] }
}
