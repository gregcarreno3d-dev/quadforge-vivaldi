//! Small vector math and geometry helpers. No dependencies.
pub mod aabb;
pub mod complex;
pub mod geom;
pub mod vec3;

pub use aabb::Aabb;
pub use complex::Cplx;
pub use vec3::Vec3;

pub const PI: f64 = std::f64::consts::PI;
pub const INF: f64 = f64::INFINITY;

#[inline]
pub fn clamp(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo { lo } else if v > hi { hi } else { v }
}
