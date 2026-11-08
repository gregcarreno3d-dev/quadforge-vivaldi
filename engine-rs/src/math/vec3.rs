use std::ops::{Add, AddAssign, Div, Index, IndexMut, Mul, MulAssign, Neg, Sub, SubAssign};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Vec3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Vec3 {
    pub const ZERO: Vec3 = Vec3 { x: 0.0, y: 0.0, z: 0.0 };
    #[inline]
    pub const fn new(x: f64, y: f64, z: f64) -> Self { Vec3 { x, y, z } }
    #[inline]
    pub fn dot(self, o: Vec3) -> f64 { self.x * o.x + self.y * o.y + self.z * o.z }
    #[inline]
    pub fn cross(self, o: Vec3) -> Vec3 {
        Vec3::new(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)
    }
    #[inline]
    pub fn length2(self) -> f64 { self.dot(self) }
    #[inline]
    pub fn length(self) -> f64 { self.dot(self).sqrt() }
    #[inline]
    pub fn distance(self, o: Vec3) -> f64 { (self - o).length() }
    #[inline]
    pub fn distance2(self, o: Vec3) -> f64 { (self - o).length2() }
    /// Unit vector, or zero when the length is (almost) zero.
    #[inline]
    pub fn normalized(self) -> Vec3 {
        let l = self.length();
        if l > 1e-300 { self / l } else { Vec3::ZERO }
    }
    #[inline]
    pub fn lerp(self, o: Vec3, t: f64) -> Vec3 { self + (o - self) * t }
}

impl Add for Vec3 { type Output = Vec3; #[inline] fn add(self, o: Vec3) -> Vec3 { Vec3::new(self.x + o.x, self.y + o.y, self.z + o.z) } }
impl Sub for Vec3 { type Output = Vec3; #[inline] fn sub(self, o: Vec3) -> Vec3 { Vec3::new(self.x - o.x, self.y - o.y, self.z - o.z) } }
impl Mul<f64> for Vec3 { type Output = Vec3; #[inline] fn mul(self, s: f64) -> Vec3 { Vec3::new(self.x * s, self.y * s, self.z * s) } }
impl Div<f64> for Vec3 { type Output = Vec3; #[inline] fn div(self, s: f64) -> Vec3 { Vec3::new(self.x / s, self.y / s, self.z / s) } }
impl Neg for Vec3 { type Output = Vec3; #[inline] fn neg(self) -> Vec3 { Vec3::new(-self.x, -self.y, -self.z) } }
impl AddAssign for Vec3 { #[inline] fn add_assign(&mut self, o: Vec3) { *self = *self + o; } }
impl SubAssign for Vec3 { #[inline] fn sub_assign(&mut self, o: Vec3) { *self = *self - o; } }
impl MulAssign<f64> for Vec3 { #[inline] fn mul_assign(&mut self, s: f64) { *self = *self * s; } }
impl Index<usize> for Vec3 {
    type Output = f64;
    #[inline]
    fn index(&self, i: usize) -> &f64 { match i { 0 => &self.x, 1 => &self.y, _ => &self.z } }
}
impl IndexMut<usize> for Vec3 {
    #[inline]
    fn index_mut(&mut self, i: usize) -> &mut f64 { match i { 0 => &mut self.x, 1 => &mut self.y, _ => &mut self.z } }
}
