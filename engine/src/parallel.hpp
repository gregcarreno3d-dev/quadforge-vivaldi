// Forge engine - tiny parallel-for on std::thread (no dependencies).
#pragma once
#include <algorithm>
#include <thread>
#include <vector>

namespace forge {

// fn(begin, end, threadIndex) is called on disjoint ranges that together cover [0, n).
template <class Fn>
void parallelFor(int n, int threads, Fn&& fn) {
    if (n <= 0) return;
    threads = std::max(1, std::min(threads, n / 1024 + 1));
    if (threads == 1) { fn(0, n, 0); return; }
    std::vector<std::thread> pool;
    pool.reserve(threads - 1);
    const int chunk = (n + threads - 1) / threads;
    for (int t = 1; t < threads; ++t) {
        const int b = t * chunk, e = std::min(n, b + chunk);
        if (b >= e) break;
        pool.emplace_back([&fn, b, e, t] { fn(b, e, t); });
    }
    fn(0, std::min(n, chunk), 0);
    for (auto& th : pool) th.join();
}

inline int defaultThreads() {
    const unsigned h = std::thread::hardware_concurrency();
    return h == 0 ? 2 : static_cast<int>(std::min(h, 16u));
}

}  // namespace forge
