//! Minimal complex number (the engine needs nothing more than this).
use std::ops::{Add, AddAssign, Div, Mul, MulAssign, Sub, SubAssign};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Cplx {
    pub re: f64,
    pub im: f64,
}

impl Cplx {
    pub const ONE: Cplx = Cplx { re: 1.0, im: 0.0 };
    pub const ZERO: Cplx = Cplx { re: 0.0, im: 0.0 };
    #[inline] pub const fn new(re: f64, im: f64) -> Self { Cplx { re, im } }
    #[inline] pub fn polar(r: f64, theta: f64) -> Self { Cplx::new(r * theta.cos(), r * theta.sin()) }
    #[inline] pub fn conj(self) -> Self { Cplx::new(self.re, -self.im) }
    #[inline] pub fn norm(self) -> f64 { self.re * self.re + self.im * self.im }
    #[inline] pub fn abs(self) -> f64 { self.norm().sqrt() }
    #[inline] pub fn arg(self) -> f64 { self.im.atan2(self.re) }
}

impl Add for Cplx { type Output = Cplx; #[inline] fn add(self, o: Cplx) -> Cplx { Cplx::new(self.re + o.re, self.im + o.im) } }
impl Sub for Cplx { type Output = Cplx; #[inline] fn sub(self, o: Cplx) -> Cplx { Cplx::new(self.re - o.re, self.im - o.im) } }
impl Mul for Cplx { type Output = Cplx; #[inline] fn mul(self, o: Cplx) -> Cplx { Cplx::new(self.re * o.re - self.im * o.im, self.re * o.im + self.im * o.re) } }
impl Mul<f64> for Cplx { type Output = Cplx; #[inline] fn mul(self, s: f64) -> Cplx { Cplx::new(self.re * s, self.im * s) } }
impl Div<f64> for Cplx { type Output = Cplx; #[inline] fn div(self, s: f64) -> Cplx { Cplx::new(self.re / s, self.im / s) } }
impl AddAssign for Cplx { #[inline] fn add_assign(&mut self, o: Cplx) { *self = *self + o; } }
impl SubAssign for Cplx { #[inline] fn sub_assign(&mut self, o: Cplx) { *self = *self - o; } }
impl MulAssign<f64> for Cplx { #[inline] fn mul_assign(&mut self, s: f64) { *self = *self * s; } }
