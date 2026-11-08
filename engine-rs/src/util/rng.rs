//! MT19937 (same sequence as C++ `std::mt19937`) so results stay comparable with the C++ engine.
pub struct Mt19937 { mt: [u32; 624], idx: usize }

impl Mt19937 {
    pub fn new(seed: u32) -> Self {
        let mut mt = [0u32; 624];
        mt[0] = seed;
        for i in 1..624 {
            mt[i] = 1812433253u32.wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_add(i as u32);
        }
        Mt19937 { mt, idx: 624 }
    }

    pub fn next_u32(&mut self) -> u32 {
        if self.idx >= 624 {
            for i in 0..624 {
                let y = (self.mt[i] & 0x8000_0000) | (self.mt[(i + 1) % 624] & 0x7fff_ffff);
                let mut v = self.mt[(i + 397) % 624] ^ (y >> 1);
                if y & 1 != 0 { v ^= 0x9908_b0df; }
                self.mt[i] = v;
            }
            self.idx = 0;
        }
        let mut y = self.mt[self.idx];
        self.idx += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    /// Uniform in [0, 1) like `std::uniform_real_distribution<double>` (two draws for 53 bits).
    pub fn next_f64(&mut self) -> f64 {
        let lo = self.next_u32() as f64;
        let hi = self.next_u32() as f64;
        let r = (lo + hi * 4294967296.0) / 18446744073709551616.0;
        if r >= 1.0 { 1.0 - f64::EPSILON / 2.0 } else { r }
    }
}
