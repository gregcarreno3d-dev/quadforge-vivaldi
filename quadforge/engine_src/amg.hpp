// Forge engine - sparse matrices and a smoothed-aggregation algebraic multigrid preconditioner.
//
// Used by param.hpp: the parametrization system is a (very large, badly conditioned) Laplacian-like matrix
// over blocks of two unknowns (u, v). Plain conjugate gradient needs thousands of iterations on it; one V-cycle
// per iteration brings that down to tens.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <memory>
#include <vector>

namespace forge {

struct Csr {
    int n = 0;                       // rows (square matrices only in this file)
    std::vector<int> start, col;
    std::vector<double> val;

    // triplets (row, col, value) with duplicates summed; rows/cols are doubles only to keep call sites short
    void build(int size, std::vector<std::array<double, 3>>& trip) {
        n = size;
        std::sort(trip.begin(), trip.end(), [](const std::array<double, 3>& a, const std::array<double, 3>& b) {
            return a[0] != b[0] ? a[0] < b[0] : a[1] < b[1];
        });
        start.assign(n + 1, 0);
        col.clear(); val.clear();
        size_t i = 0;
        while (i < trip.size()) {
            size_t j = i;
            double s = 0;
            while (j < trip.size() && trip[j][0] == trip[i][0] && trip[j][1] == trip[i][1]) { s += trip[j][2]; ++j; }
            col.push_back(static_cast<int>(trip[i][1]));
            val.push_back(s);
            start[static_cast<int>(trip[i][0]) + 1]++;
            i = j;
        }
        for (int r = 0; r < n; ++r) start[r + 1] += start[r];
    }
    void mul(const std::vector<double>& x, std::vector<double>& y) const {
        for (int r = 0; r < n; ++r) {
            double s = 0;
            for (int k = start[r]; k < start[r + 1]; ++k) s += val[k] * x[col[k]];
            y[r] = s;
        }
    }
};

namespace amgdetail {

struct Rect {                         // rectangular sparse matrix (rows x cols), CSR
    int rows = 0, cols = 0;
    std::vector<int> start, col;
    std::vector<double> val;
};

inline Rect transpose(const Rect& a) {
    Rect t;
    t.rows = a.cols; t.cols = a.rows;
    t.start.assign(t.rows + 1, 0);
    for (int c : a.col) t.start[c + 1]++;
    for (int r = 0; r < t.rows; ++r) t.start[r + 1] += t.start[r];
    t.col.resize(a.col.size());
    t.val.resize(a.val.size());
    std::vector<int> fill(t.start.begin(), t.start.end() - 1);
    for (int r = 0; r < a.rows; ++r)
        for (int k = a.start[r]; k < a.start[r + 1]; ++k) {
            const int p = fill[a.col[k]]++;
            t.col[p] = r;
            t.val[p] = a.val[k];
        }
    return t;
}

// C = A * B
inline Rect multiply(const Rect& a, const Rect& b) {
    Rect c;
    c.rows = a.rows; c.cols = b.cols;
    c.start.assign(c.rows + 1, 0);
    std::vector<int> mark(b.cols, -1);
    std::vector<double> acc(b.cols, 0.0);
    std::vector<int> cols;
    for (int r = 0; r < a.rows; ++r) {
        cols.clear();
        for (int k = a.start[r]; k < a.start[r + 1]; ++k) {
            const int m = a.col[k];
            const double av = a.val[k];
            for (int q = b.start[m]; q < b.start[m + 1]; ++q) {
                const int j = b.col[q];
                if (mark[j] != r) { mark[j] = r; acc[j] = 0; cols.push_back(j); }
                acc[j] += av * b.val[q];
            }
        }
        std::sort(cols.begin(), cols.end());
        for (int j : cols) { c.col.push_back(j); c.val.push_back(acc[j]); }
        c.start[r + 1] = static_cast<int>(c.col.size());
    }
    return c;
}

inline void mulVec(const Rect& a, const std::vector<double>& x, std::vector<double>& y) {
    y.assign(a.rows, 0.0);
    for (int r = 0; r < a.rows; ++r) {
        double s = 0;
        for (int k = a.start[r]; k < a.start[r + 1]; ++k) s += a.val[k] * x[a.col[k]];
        y[r] = s;
    }
}

}  // namespace amgdetail

class Amg {
public:
    // K must be symmetric positive definite (add a small shift beforehand when it is only semi-definite).
    // Unknowns come in blocks of `bs` (2 here); aggregates are made over blocks.
    // G: the part of K that couples unknowns in the same frame (used for aggregation and prolongator smoothing)
    void build(const Csr& K, const Csr& G, int bs, int maxLevels = 12, int coarseSize = 300) {
        levels_.clear();
        bs_ = bs;
        Level L0;
        L0.A.rows = L0.A.cols = K.n;
        L0.A.start = K.start; L0.A.col = K.col; L0.A.val = K.val;
        L0.G.rows = L0.G.cols = G.n;
        L0.G.start = G.start; L0.G.col = G.col; L0.G.val = G.val;
        levels_.push_back(std::move(L0));
        for (int l = 0; l + 1 < maxLevels; ++l) {
            Level& cur = levels_.back();
            finishLevel(cur);
            if (cur.A.rows <= coarseSize) break;
            amgdetail::Rect P;
            if (!aggregate(cur, P)) break;
            amgdetail::Rect Pt = amgdetail::transpose(P);
            amgdetail::Rect AP = amgdetail::multiply(cur.A, P);
            Level nxt;
            nxt.A = amgdetail::multiply(Pt, AP);
            nxt.G = amgdetail::multiply(Pt, amgdetail::multiply(cur.G, P));
            cur.P = std::move(P);
            cur.R = std::move(Pt);
            const int nc = nxt.A.rows;
            if (nc >= cur.A.rows * 9 / 10) { levels_.push_back(std::move(nxt)); break; }
            levels_.push_back(std::move(nxt));
        }
        finishLevel(levels_.back());
        // dense Cholesky of the coarsest matrix
        Level& c = levels_.back();
        const int n = c.A.rows;
        dense_.assign(static_cast<size_t>(n) * n, 0.0);
        for (int r = 0; r < n; ++r)
            for (int k = c.A.start[r]; k < c.A.start[r + 1]; ++k) dense_[static_cast<size_t>(r) * n + c.A.col[k]] = c.A.val[k];
        for (int r = 0; r < n; ++r) dense_[static_cast<size_t>(r) * n + r] *= 1.0 + 1e-10;
        densN_ = n;
        // in place Cholesky (lower)
        for (int j = 0; j < n; ++j) {
            double d = dense_[static_cast<size_t>(j) * n + j];
            for (int k = 0; k < j; ++k) d -= dense_[static_cast<size_t>(j) * n + k] * dense_[static_cast<size_t>(j) * n + k];
            if (d < 1e-300) d = 1e-300;
            d = std::sqrt(d);
            dense_[static_cast<size_t>(j) * n + j] = d;
            for (int i = j + 1; i < n; ++i) {
                double s = dense_[static_cast<size_t>(i) * n + j];
                for (int k = 0; k < j; ++k) s -= dense_[static_cast<size_t>(i) * n + k] * dense_[static_cast<size_t>(j) * n + k];
                dense_[static_cast<size_t>(i) * n + j] = s / d;
            }
        }
    }

    int numLevels() const { return static_cast<int>(levels_.size()); }
    int coarseRows() const { return levels_.empty() ? 0 : levels_.back().A.rows; }

    // z = approximately K^{-1} r (one V-cycle, symmetric)
    void apply(const std::vector<double>& r, std::vector<double>& z) {
        z.assign(r.size(), 0.0);
        vcycle(0, r, z);
    }

private:
    struct Level {
        amgdetail::Rect A, G, P, R;
        std::vector<double> diag, gdiag;
        std::vector<double> rr, xx, tmp;      // scratch
    };
    std::vector<Level> levels_;
    std::vector<double> dense_;
    int densN_ = 0;
    int bs_ = 2;

    void finishLevel(Level& L) {
        const int n = L.A.rows;
        L.diag.assign(n, 1.0);
        for (int r = 0; r < n; ++r)
            for (int k = L.A.start[r]; k < L.A.start[r + 1]; ++k) if (L.A.col[k] == r) L.diag[r] = std::max(L.A.val[k], 1e-300);
        L.gdiag.assign(n, 1.0);
        for (int r = 0; r < n; ++r)
            for (int k = L.G.start[r]; k < L.G.start[r + 1]; ++k) if (L.G.col[k] == r) L.gdiag[r] = std::max(L.G.val[k], 1e-300);
        L.rr.assign(n, 0.0); L.xx.assign(n, 0.0); L.tmp.assign(n, 0.0);
    }

    // smoothed aggregation: tentative prolongator is block-constant per aggregate, then one damped Jacobi step
    bool aggregate(const Level& L, amgdetail::Rect& P) {
        const int n = L.G.rows;
        const int bs = bs_;
        const int nb = n / bs;
        // block graph strength: connections between blocks i != j whose coupling is not negligible
        std::vector<int> agg(nb, -1);
        auto strong = [&](int bi, int bj, double thr) {   // coupling of block bi to bj relative to the diagonals
            double s = 0;
            for (int a = 0; a < bs; ++a) {
                const int r = bi * bs + a;
                for (int k = L.G.start[r]; k < L.G.start[r + 1]; ++k) {
                    if (L.G.col[k] / bs == bj) s = std::max(s, std::fabs(L.G.val[k]) / std::sqrt(L.gdiag[r] * L.gdiag[L.G.col[k]]));
                }
            }
            return s > thr;
        };
        const double thr = 0.08;
        int na = 0;
        for (int bi = 0; bi < nb; ++bi) {
            if (agg[bi] >= 0) continue;
            // only seed an aggregate when all strong neighbours are free
            std::vector<int> nbrs;
            bool free = true;
            for (int a = 0; a < bs && free; ++a) {
                const int r = bi * bs + a;
                for (int k = L.G.start[r]; k < L.G.start[r + 1]; ++k) {
                    const int bj = L.G.col[k] / bs;
                    if (bj == bi) continue;
                    if (std::find(nbrs.begin(), nbrs.end(), bj) != nbrs.end()) continue;
                    if (!strong(bi, bj, thr)) continue;
                    nbrs.push_back(bj);
                    if (agg[bj] >= 0) { free = false; break; }
                }
            }
            if (!free) continue;
            agg[bi] = na;
            for (int bj : nbrs) agg[bj] = na;
            ++na;
        }
        // leftovers join the aggregate of a strong neighbour (or form their own)
        for (int bi = 0; bi < nb; ++bi) {
            if (agg[bi] >= 0) continue;
            int best = -1;
            double bestS = 0;
            for (int a = 0; a < bs; ++a) {
                const int r = bi * bs + a;
                for (int k = L.G.start[r]; k < L.G.start[r + 1]; ++k) {
                    const int bj = L.G.col[k] / bs;
                    if (bj == bi || agg[bj] < 0) continue;
                    const double s = std::fabs(L.G.val[k]) / std::sqrt(L.gdiag[r] * L.gdiag[L.G.col[k]]);
                    if (s > bestS) { bestS = s; best = agg[bj]; }
                }
            }
            if (best >= 0) agg[bi] = best; else agg[bi] = na++;
        }
        if (na >= nb) return false;
        // tentative prolongator
        amgdetail::Rect T;
        T.rows = n; T.cols = na * bs;
        T.start.assign(n + 1, 0);
        for (int r = 0; r < n; ++r) T.start[r + 1] = r + 1;
        T.col.resize(n); T.val.assign(n, 1.0);
        for (int r = 0; r < n; ++r) T.col[r] = agg[r / bs] * bs + (r % bs);
        // P = (I - w D^-1 A) T
        const double w = 2.0 / 3.0;
        amgdetail::Rect S;
        S.rows = S.cols = n;
        S.start.assign(n + 1, 0);
        for (int r = 0; r < n; ++r) {
            for (int k = L.G.start[r]; k < L.G.start[r + 1]; ++k) {
                const int c = L.G.col[k];
                double v = -w * L.G.val[k] / L.gdiag[r];
                if (c == r) v += 1.0;
                S.col.push_back(c); S.val.push_back(v);
            }
            S.start[r + 1] = static_cast<int>(S.col.size());
        }
        P = amgdetail::multiply(S, T);
        return true;
    }

    static void gsForward(const amgdetail::Rect& A, const std::vector<double>& d, const std::vector<double>& b, std::vector<double>& x) {
        const int n = A.rows;
        for (int r = 0; r < n; ++r) {
            double s = b[r];
            for (int k = A.start[r]; k < A.start[r + 1]; ++k) if (A.col[k] != r) s -= A.val[k] * x[A.col[k]];
            x[r] = s / d[r];
        }
    }
    static void gsBackward(const amgdetail::Rect& A, const std::vector<double>& d, const std::vector<double>& b, std::vector<double>& x) {
        for (int r = A.rows - 1; r >= 0; --r) {
            double s = b[r];
            for (int k = A.start[r]; k < A.start[r + 1]; ++k) if (A.col[k] != r) s -= A.val[k] * x[A.col[k]];
            x[r] = s / d[r];
        }
    }

    void vcycle(int l, const std::vector<double>& b, std::vector<double>& x) {
        Level& L = levels_[l];
        if (l + 1 == static_cast<int>(levels_.size())) {
            const int n = densN_;
            std::vector<double>& y = L.tmp;
            y = b;
            for (int i = 0; i < n; ++i) {
                double s = y[i];
                for (int k = 0; k < i; ++k) s -= dense_[static_cast<size_t>(i) * n + k] * y[k];
                y[i] = s / dense_[static_cast<size_t>(i) * n + i];
            }
            for (int i = n - 1; i >= 0; --i) {
                double s = y[i];
                for (int k = i + 1; k < n; ++k) s -= dense_[static_cast<size_t>(k) * n + i] * y[k];
                y[i] = s / dense_[static_cast<size_t>(i) * n + i];
            }
            x = y;
            return;
        }
        gsForward(L.A, L.diag, b, x);                       // pre-smoothing (x starts at zero)
        // residual
        std::vector<double>& res = L.rr;
        amgdetail::mulVec(L.A, x, res);
        for (size_t i = 0; i < res.size(); ++i) res[i] = b[i] - res[i];
        std::vector<double> rc, xc(levels_[l + 1].A.rows, 0.0);
        amgdetail::mulVec(L.R, res, rc);
        vcycle(l + 1, rc, xc);
        std::vector<double> corr;
        amgdetail::mulVec(L.P, xc, corr);
        for (size_t i = 0; i < x.size(); ++i) x[i] += corr[i];
        gsBackward(L.A, L.diag, b, x);                      // post-smoothing
    }
};

}  // namespace forge
