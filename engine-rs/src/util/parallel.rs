//! Tiny parallel-for on scoped std threads (no dependencies).
use std::thread;

/// Calls `f(begin, end, thread_index)` on disjoint ranges that together cover `[0, n)`.
pub fn parallel_for<F>(n: usize, threads: usize, f: F)
where
    F: Fn(usize, usize, usize) + Sync,
{
    if n == 0 { return; }
    let threads = threads.max(1).min(n / 1024 + 1);
    if threads == 1 { f(0, n, 0); return; }
    let chunk = n.div_ceil(threads);
    thread::scope(|s| {
        for t in 1..threads {
            let b = t * chunk;
            let e = (b + chunk).min(n);
            if b >= e { break; }
            let f = &f;
            s.spawn(move || f(b, e, t));
        }
        f(0, chunk.min(n), 0);
    });
}

pub fn default_threads() -> usize {
    thread::available_parallelism().map(|n| n.get().min(16)).unwrap_or(2)
}

/// Fills `out[i] = f(i)` in parallel on disjoint chunks.
pub fn par_fill<T, F>(out: &mut [T], threads: usize, f: F)
where
    T: Send,
    F: Fn(usize) -> T + Sync,
{
    let n = out.len();
    if n == 0 { return; }
    let threads = threads.max(1).min(n / 1024 + 1);
    if threads == 1 {
        for (i, o) in out.iter_mut().enumerate() { *o = f(i); }
        return;
    }
    let chunk = n.div_ceil(threads);
    thread::scope(|s| {
        for (ci, part) in out.chunks_mut(chunk).enumerate() {
            let f = &f;
            s.spawn(move || {
                let base = ci * chunk;
                for (k, o) in part.iter_mut().enumerate() { *o = f(base + k); }
            });
        }
    });
}
