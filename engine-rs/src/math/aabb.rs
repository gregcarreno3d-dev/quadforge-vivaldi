use super::{vec3::Vec3, INF};

#[derive(Clone, Copy, Debug)]
pub struct Aabb {
    pub lo: Vec3,
    pub hi: Vec3,
}

impl Default for Aabb {
    fn default() -> Self { Aabb { lo: Vec3::new(INF, INF, INF), hi: Vec3::new(-INF, -INF, -INF) } }
}

impl Aabb {
    pub fn expand_point(&mut self, p: Vec3) {
        self.lo = Vec3::new(self.lo.x.min(p.x), self.lo.y.min(p.y), self.lo.z.min(p.z));
        self.hi = Vec3::new(self.hi.x.max(p.x), self.hi.y.max(p.y), self.hi.z.max(p.z));
    }
    pub fn expand(&mut self, b: &Aabb) {
        self.expand_point(b.lo);
        self.expand_point(b.hi);
    }
    pub fn center(&self) -> Vec3 { (self.lo + self.hi) * 0.5 }
    pub fn extent(&self) -> Vec3 { self.hi - self.lo }
    pub fn distance2_to(&self, p: Vec3) -> f64 {
        let dx = (self.lo.x - p.x).max(0.0).max(p.x - self.hi.x);
        let dy = (self.lo.y - p.y).max(0.0).max(p.y - self.hi.y);
        let dz = (self.lo.z - p.z).max(0.0).max(p.z - self.hi.z);
        dx * dx + dy * dy + dz * dz
    }
}
