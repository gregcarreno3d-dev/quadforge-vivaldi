// Forge engine - seamless integer-grid parametrization (a compact mixed-integer quadrangulation).
//
// Input: a closed triangle mesh and its face cross field. Output: (u,v) at every triangle corner such
// that the iso-lines u = integer, v = integer form the quad layout (one lattice cell per rho x rho).
//
//   1. Field matching: neighbouring faces agree on the 4 directions up to a rotation of k * 90 degrees.
//      Vertices where the rotations around do not close are the singularities (poles).
//   2. Gauge: one of the four directions per face is chosen by propagating along a spanning tree of the
//      dual graph. Edges where the choice still jumps by k != 0 form the cut graph (it ends at the
//      singularities); on a surface with handles a few extra cycles are added.
//   3. Sheets: corners around a vertex that are not separated by cut edges share one (u,v) pair.
//   4. Least squares: grad u = U / rho, grad v = V / rho on every face (U, V = the chosen field
//      directions). Across a cut edge the two sides must agree up to (u,v)_B = Rot(k)(u,v)_A + t.
//      The agreement is a stiff penalty; t is a free translation per cut edge.
//   5. Integer rounding: the translations t and the (u,v) of the singular vertices must be integers.
//      They are rounded gradually (the nearest first), solving again after each batch.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <atomic>
#include <thread>
#include <chrono>
#include <functional>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <numeric>
#include <queue>
#include <string>
#include <unordered_map>
#include <vector>

#include "amg.hpp"
#include "cmesh.hpp"
#include "field.hpp"

namespace forge {

struct ParamResult {
    std::vector<std::array<std::array<double, 2>, 3>> uv;   // per face corner
    std::vector<Vec3> U, V;                                 // chosen field directions per face
    std::vector<int> singular;                              // vertex ids
    int cutEdges = 0, sheets = 0, genusLoops = 0, solves = 0;
    double faceError = 0;      // mean |grad - target| * rho
    double seamError = 0;      // max |seam residual|
    int badSeams = 0;          // seams whose residual is above 0.25 of a cell (an integer defect at a cut junction)
    double seconds = 0;
    std::vector<double> warm;                               // real-valued solution, reused when rho changes
    double warmRho = 0;
    double densityPower = 1.0;  // the density (size multiplier) was raised to this power (1 = full, 0 = ignored)
    std::string error;
};

class IntegerParam {
public:
    struct Options { bool alignFeatures; double densityPower; std::function<void(int, const std::string&)> progress; double timeLimit; int threads; Options() : alignFeatures(false), densityPower(1.0), timeLimit(0.0), threads(1) {} };
    IntegerParam(const CMesh& m, const FaceFrames& fr, const std::vector<cplx>& z, Options o = Options()) : m_(m), fr_(fr), z_(z), prm_(o) {}
    bool run(double rho, ParamResult& out);

private:
    const CMesh& m_;
    const FaceFrames& fr_;
    const std::vector<cplx>& z_;
    Options prm_;

    static int mod4(int a) { return ((a % 4) + 4) % 4; }
    // Conjugate gradient on the free variables (fixed ones keep their value).
    int solve(const Csr& K, const std::vector<double>& b, std::vector<double>& x, const std::vector<uint8_t>& fixedMask,
              const std::vector<double>& diag, int maxIter, double tol, Amg* pre) const;
};

namespace paramdetail {

// reusable spinning barrier for a fixed team of threads
struct Barrier {
    std::atomic<int> count{0};
    std::atomic<int> gen{0};
    int n;
    explicit Barrier(int teamSize) : n(teamSize) {}
    void wait() {
        const int g = gen.load(std::memory_order_acquire);
        if (count.fetch_add(1, std::memory_order_acq_rel) + 1 == n) {
            count.store(0, std::memory_order_relaxed);
            gen.fetch_add(1, std::memory_order_acq_rel);
        } else {
            int spins = 0;
            while (gen.load(std::memory_order_acquire) == g) { if (++spins > 2000) std::this_thread::yield(); }
        }
    }
};

// Jacobi preconditioned conjugate gradient with the whole iteration run by a team of threads
// (rows are split in fixed chunks, so the result does not depend on timing).
inline int parallelCG(const Csr& K, const std::vector<double>& b, std::vector<double>& x, const std::vector<uint8_t>& fixedMask,
                      const std::vector<double>& diag, int maxIter, double tol, int threads) {
    const int n = static_cast<int>(x.size());
    const int T = std::max(1, threads);
    std::vector<double> r(n, 0.0), p(n, 0.0), Ap(n, 0.0), zv(n, 0.0);
    std::vector<double> partA(T * 8, 0.0), partB(T * 8, 0.0), partC(T * 8, 0.0), partD(T * 8, 0.0);
    Barrier bar(T);
    std::atomic<int> itsDone{0};
    const int chunk = (n + T - 1) / T;
    auto worker = [&](int t) {
        const int rb = std::min(n, t * chunk), re = std::min(n, rb + chunk);
        double bn = 0;
        for (int i = rb; i < re; ++i) {
            double s = 0;
            for (int k = K.start[i]; k < K.start[i + 1]; ++k) s += K.val[k] * x[K.col[k]];
            if (fixedMask[i]) { r[i] = 0; zv[i] = 0; p[i] = 0; continue; }
            r[i] = b[i] - s;
            bn += b[i] * b[i];
        }
        partA[t * 8] = bn;
        bar.wait();
        double bnorm = 0;
        for (int q = 0; q < T; ++q) bnorm += partA[q * 8];
        double rzl = 0;
        for (int i = rb; i < re; ++i) {
            if (fixedMask[i]) continue;
            zv[i] = r[i] / diag[i];
            p[i] = zv[i];
            rzl += r[i] * zv[i];
        }
        partB[t * 8] = rzl;
        bar.wait();
        double rz = 0;
        for (int q = 0; q < T; ++q) rz += partB[q * 8];
        if (rz < 1e-300) { if (t == 0) itsDone = 0; return; }
        int it = 0;
        for (; it < maxIter; ++it) {
            double pAp = 0;
            for (int i = rb; i < re; ++i) {
                double s = 0;
                for (int k = K.start[i]; k < K.start[i + 1]; ++k) s += K.val[k] * p[K.col[k]];
                if (fixedMask[i]) s = 0;
                Ap[i] = s;
                pAp += p[i] * s;
            }
            partC[t * 8] = pAp;
            bar.wait();
            double tot = 0;
            for (int q = 0; q < T; ++q) tot += partC[q * 8];
            if (std::fabs(tot) < 1e-300) break;
            const double alpha = rz / tot;
            double rn = 0, rzNew = 0;
            for (int i = rb; i < re; ++i) {
                if (fixedMask[i]) continue;
                x[i] += alpha * p[i];
                r[i] -= alpha * Ap[i];
                rn += r[i] * r[i];
                zv[i] = r[i] / diag[i];
                rzNew += r[i] * zv[i];
            }
            partD[t * 8] = rn;
            partB[t * 8] = rzNew;
            bar.wait();
            double rnT = 0, rzT = 0;
            for (int q = 0; q < T; ++q) { rnT += partD[q * 8]; rzT += partB[q * 8]; }
            if (rnT <= tol * tol * std::max(bnorm, 1.0)) { ++it; break; }
            const double beta = rzT / rz;
            rz = rzT;
            for (int i = rb; i < re; ++i) p[i] = fixedMask[i] ? 0 : zv[i] + beta * p[i];
            bar.wait();
        }
        if (t == 0) itsDone = it;
    };
    std::vector<std::thread> pool;
    for (int t = 1; t < T; ++t) pool.emplace_back(worker, t);
    worker(0);
    for (auto& th : pool) th.join();
    return itsDone.load();
}

}  // namespace paramdetail

inline int IntegerParam::solve(const Csr& K, const std::vector<double>& b, std::vector<double>& x,
                               const std::vector<uint8_t>& fixedMask, const std::vector<double>& diag, int maxIter,
                               double tol, Amg* pre) const {
    const int n = static_cast<int>(x.size());
    if (!pre && prm_.threads > 1 && n >= 40000) return paramdetail::parallelCG(K, b, x, fixedMask, diag, maxIter, tol, std::min(prm_.threads, 1 + n / 20000));
    std::vector<double> r(n), p(n), Ap(n), zv(n), rm(n);
    auto precond = [&]() {
        if (!pre) { for (int i = 0; i < n; ++i) zv[i] = fixedMask[i] ? 0 : r[i] / diag[i]; return; }
        for (int i = 0; i < n; ++i) rm[i] = fixedMask[i] ? 0 : r[i];
        pre->apply(rm, zv);
        for (int i = 0; i < n; ++i) if (fixedMask[i]) zv[i] = 0;
    };
    K.mul(x, Ap);
    double bnorm = 0;
    for (int i = 0; i < n; ++i) {
        if (fixedMask[i]) { r[i] = 0; continue; }
        r[i] = b[i] - Ap[i];
        bnorm += b[i] * b[i];
    }
    double rz = 0;
    precond();
    for (int i = 0; i < n; ++i) { p[i] = zv[i]; rz += r[i] * zv[i]; }
    if (rz < 1e-300) return 0;
    int it = 0;
    for (; it < maxIter; ++it) {
        K.mul(p, Ap);
        double pAp = 0;
        for (int i = 0; i < n; ++i) { if (fixedMask[i]) Ap[i] = 0; pAp += p[i] * Ap[i]; }
        if (std::fabs(pAp) < 1e-300) break;
        const double alpha = rz / pAp;
        double rn = 0;
        for (int i = 0; i < n; ++i) {
            if (fixedMask[i]) continue;
            x[i] += alpha * p[i];
            r[i] -= alpha * Ap[i];
            rn += r[i] * r[i];
        }
        if (rn <= tol * tol * std::max(bnorm, 1.0)) { ++it; break; }
        double rzNew = 0;
        precond();
        for (int i = 0; i < n; ++i) rzNew += r[i] * zv[i];
        const double beta = rzNew / rz;
        rz = rzNew;
        for (int i = 0; i < n; ++i) p[i] = fixedMask[i] ? 0 : zv[i] + beta * p[i];
    }
    return it;
}

inline bool IntegerParam::run(double rho, ParamResult& out) {
    out.genusLoops = 0; out.solves = 0; out.seamError = 0; out.densityPower = prm_.densityPower;
    auto T0 = std::chrono::steady_clock::now();
    auto lap = [&](const char* what) {
        if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: %-12s %.2fs (solves %d)\n", what, std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count(), out.solves);
        if (prm_.progress) {
            static const std::pair<const char*, std::pair<int, const char*>> table[] = {
                {"topology", {44, "Preparing the quad grid"}}, {"assembled", {46, "Solving the quad grid"}}, {"real solve", {56, "Placing the singular points"}},
                {"singular", {61, "Fixing the grid seams"}}, {"stiff", {65, "Fixing the grid seams"}}, {"feature lines", {71, "Aligning to hard edges"}},
                {"rounded", {75, "Finishing the grid"}}, {"final", {77, "Finishing the grid"}}};
            for (const auto& e : table) if (std::strcmp(e.first, what) == 0) prm_.progress(e.second.first, e.second.second);
        }
    };
    auto timeUp = [&]() { return prm_.timeLimit > 0 && std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count() > prm_.timeLimit; };
    const int nF = m_.numFaceSlots();
    const int nVs = m_.numVertSlots();
    const int nC = 3 * nF;
    // ---- closed surface only ----
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        for (int k = 0; k < 3; ++k) if (m_.O[3 * f + k] < 0) { out.error = "the surface has a border"; return false; }
    }
    // ---- edges ----
    std::vector<int> edgeOfCorner(nC, -1);
    std::vector<int> ec0;                    // canonical corner of each edge
    for (int c = 0; c < nC; ++c) {
        if (!m_.falive[c / 3]) continue;
        const int d = m_.O[c];
        if (d < c) continue;                 // canonical = smaller corner
        edgeOfCorner[c] = edgeOfCorner[d] = static_cast<int>(ec0.size());
        ec0.push_back(c);
    }
    const int nE = static_cast<int>(ec0.size());
    auto endA = [&](int e) { return m_.V[CMesh::nxt(ec0[e])]; };
    auto endB = [&](int e) { return m_.V[CMesh::prv(ec0[e])]; };

    // ---- matching between faces ----
    std::vector<double> theta(nF, 0.0);
    for (int f = 0; f < nF; ++f) if (m_.falive[f]) theta[f] = std::arg(z_[f]) / 4.0;
    const double halfPi = kPi / 2;
    auto transportAngle = [&](int c) {                     // Delta for crossing the edge opposite corner c
        const int f = c / 3, k = c % 3, d = m_.O[c], g = d / 3, kk = d % 3;
        return fr_.alpha[g][kk] + kPi - fr_.alpha[f][k];
    };
    auto matchIndex = [&](int c) {                          // r such that theta_f + Delta = theta_g + r*90deg
        const int f = c / 3, g = m_.O[c] / 3;
        return mod4(static_cast<int>(std::lround((theta[f] + transportAngle(c) - theta[g]) / halfPi)));
    };
    std::vector<int> rEdge(nE);                              // from the canonical corner's face to the twin's face
    for (int e = 0; e < nE; ++e) rEdge[e] = matchIndex(ec0[e]);

    // ---- singular vertices ----
    std::vector<int> scratch;
    std::vector<uint8_t> isSing(nVs, 0);
    for (int v = 0; v < nVs; ++v) {
        if (!m_.valive[v]) continue;
        m_.orbit(v, scratch);
        int sum = 0;
        for (int c : scratch) {
            const int ce = CMesh::prv(c);                    // edge shared with the next face around v
            const int e = edgeOfCorner[ce];
            sum += (ce == ec0[e]) ? rEdge[e] : mod4(-rEdge[e]);
        }
        if (mod4(sum) != 0) { isSing[v] = 1; out.singular.push_back(v); }
    }

    // ---- spanning trees: primal (vertices) and dual (faces) ----
    std::vector<std::vector<std::pair<int, int>>> vadj(nVs);
    for (int e = 0; e < nE; ++e) { vadj[endA(e)].push_back({endB(e), e}); vadj[endB(e)].push_back({endA(e), e}); }
    std::vector<uint8_t> inTp(nE, 0);
    std::vector<int> pedge(nVs, -1), pvert(nVs, -1);
    {
        std::vector<uint8_t> seen(nVs, 0);
        std::queue<int> q;
        auto grow = [&](int root) {
            if (seen[root]) return;
            q.push(root); seen[root] = 1;
            while (!q.empty()) {
                const int v = q.front(); q.pop();
                for (auto [w, e] : vadj[v]) if (!seen[w]) { seen[w] = 1; pedge[w] = e; pvert[w] = v; inTp[e] = 1; q.push(w); }
            }
        };
        for (int v : out.singular) grow(v);
        for (int v = 0; v < nVs; ++v) if (m_.valive[v]) grow(v);
    }
    std::vector<int> gauge(nF, -1);
    std::vector<uint8_t> inTd(nE, 0);
    std::vector<int> dualRoots;
    for (int root = 0; root < nF; ++root) {
        if (!m_.falive[root] || gauge[root] >= 0) continue;
        dualRoots.push_back(root);
        std::queue<int> q;
        q.push(root); gauge[root] = 0;
        while (!q.empty()) {
            const int f = q.front(); q.pop();
            for (int k = 0; k < 3; ++k) {
                const int c = 3 * f + k;
                const int e = edgeOfCorner[c];
                if (inTp[e]) continue;
                const int g = m_.O[c] / 3;
                if (gauge[g] >= 0) continue;
                // theta_f~ + Delta = theta_g~ + k*90  with theta~ = theta + gauge*90
                const int r = (c == ec0[e]) ? rEdge[e] : mod4(-rEdge[e]);
                gauge[g] = mod4(gauge[f] + r);
                inTd[e] = 1;
                q.push(g);
            }
        }
    }
    // ---- cut edges: jumps after the gauge, plus the generators of handles ----
    std::vector<uint8_t> cut(nE, 0);
    std::vector<int> jump(nE, 0);      // k_e: theta~_f + Delta = theta~_g + k*90 (f = face of canonical corner)
    for (int e = 0; e < nE; ++e) {
        const int c = ec0[e];
        const int f = c / 3, g = m_.O[c] / 3;
        jump[e] = mod4(rEdge[e] + gauge[f] - gauge[g]);
        if (jump[e] != 0) cut[e] = 1;
    }
    {   // the cut graph is topological: every singular vertex is connected to the root, and handles are opened
        auto addPath = [&](int v) { while (pedge[v] >= 0) { cut[pedge[v]] = 1; v = pvert[v]; } };
        for (int v : out.singular) addPath(v);
        for (int e = 0; e < nE; ++e)
            if (!inTp[e] && !inTd[e]) { cut[e] = 1; addPath(endA(e)); addPath(endB(e)); out.genusLoops++; }
    }
    // dangling cut edges (a cut vertex of degree 1 that is not singular) carry no information: drop them
    {
        std::vector<int> deg(nVs, 0);
        for (int e = 0; e < nE; ++e) if (cut[e]) { deg[endA(e)]++; deg[endB(e)]++; }
        bool changed = true;
        while (changed) {
            changed = false;
            for (int e = 0; e < nE; ++e) {
                if (!cut[e] || jump[e] != 0) continue;
                const int a = endA(e), b = endB(e);
                if ((deg[a] == 1 && !isSing[a]) || (deg[b] == 1 && !isSing[b])) { cut[e] = 0; deg[a]--; deg[b]--; changed = true; }
            }
        }
    }
    // translations are shared along a cut path (nothing changes at a vertex of cut degree two)
    int nCut = 0;
    std::vector<int> cutIndex(nE, -1);
    std::vector<std::vector<int>> segJunction;
    std::vector<uint8_t> fLeft(nE, 1);     // the canonical face lies left of the travel direction of its path
    {
        std::vector<int> cdeg(nVs, 0);
        int cutEdgesN = 0;
        for (int e = 0; e < nE; ++e) if (cut[e]) { cdeg[endA(e)]++; cdeg[endB(e)]++; ++cutEdgesN; }
        out.cutEdges = cutEdgesN;
        std::vector<int> sp(nE);
        std::iota(sp.begin(), sp.end(), 0);
        auto fs = [&](int a) { while (sp[a] != a) { sp[a] = sp[sp[a]]; a = sp[a]; } return a; };
        std::vector<int> firstCut(nVs, -1);
        auto through = [&](int v) { return cdeg[v] == 2 && !isSing[v]; };
        for (int e = 0; e < nE; ++e) {
            if (!cut[e]) continue;
            for (int v : {endA(e), endB(e)}) {
                if (!through(v)) continue;
                if (firstCut[v] < 0) firstCut[v] = e; else sp[fs(e)] = fs(firstCut[v]);
            }
        }
        std::unordered_map<int, int> segId;
        for (int e = 0; e < nE; ++e) {
            if (!cut[e]) continue;
            const int r = fs(e);
            auto it = segId.find(r);
            if (it == segId.end()) { it = segId.emplace(r, nCut++).first; segJunction.emplace_back(); }
            cutIndex[e] = it->second;
            for (int v : {endA(e), endB(e)}) if (!through(v)) segJunction[it->second].push_back(v);
        }
        std::vector<std::array<int, 2>> thr(nVs, {-1, -1});
        for (int e = 0; e < nE; ++e) {
            if (!cut[e]) continue;
            for (int v : {endA(e), endB(e)}) if (through(v)) { if (thr[v][0] < 0) thr[v][0] = e; else thr[v][1] = e; }
        }
        std::vector<uint8_t> done(nE, 0);
        auto walk = [&](int e, int w, bool forward) {   // move on from e through vertex w
            for (;;) {
                if (!through(w)) break;
                const int e2 = thr[w][0] == e ? thr[w][1] : thr[w][0];
                if (e2 < 0 || done[e2]) break;
                done[e2] = 1;
                const bool aToB = forward ? (w == endA(e2)) : (w == endB(e2));
                fLeft[e2] = aToB;
                w = (w == endA(e2)) ? endB(e2) : endA(e2);
                e = e2;
            }
        };
        for (int e = 0; e < nE; ++e) {
            if (!cut[e] || done[e]) continue;
            done[e] = 1; fLeft[e] = 1;
            walk(e, endB(e), true);
            walk(e, endA(e), false);
        }
    }

    // ---- sheets ----
    std::vector<int> par(nC);
    std::iota(par.begin(), par.end(), 0);
    auto findp = [&](int x) { while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; } return x; };
    auto uni = [&](int a, int b) { par[findp(a)] = findp(b); };
    for (int e = 0; e < nE; ++e) {
        if (cut[e]) continue;
        const int c0 = ec0[e], d0 = m_.O[c0];
        uni(CMesh::nxt(c0), CMesh::prv(d0));
        uni(CMesh::prv(c0), CMesh::nxt(d0));
    }
    std::vector<int> sheetOf(nC, -1);
    int nS = 0;
    {
        std::unordered_map<int, int> id;
        for (int c = 0; c < nC; ++c) {
            if (!m_.falive[c / 3]) continue;
            const int r = findp(c);
            auto it = id.find(r);
            if (it == id.end()) it = id.emplace(r, nS++).first;
            sheetOf[c] = it->second;
        }
    }
    out.sheets = nS;

    lap("topology");
    // ---- assemble the system ----
    const int nVar = 2 * (nS + nCut);
    std::vector<std::array<double, 3>> trip, tripS;
    trip.reserve(static_cast<size_t>(nF) * 18 + nCut * 64);
    std::vector<double> rhs(nVar, 0.0);
    std::vector<std::array<Vec3, 2>> target(nF);
    std::vector<double> faceScale(nF, 1.0);
    out.U.assign(nF, Vec3()); out.V.assign(nF, Vec3());
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        const double th = theta[f] + gauge[f] * halfPi;
        const Vec3 U = fr_.e1[f] * std::cos(th) + fr_.e2[f] * std::sin(th);
        const Vec3 Vd = cross(fr_.n[f], U);
        double sc = 1.0;
        if (!m_.vdens.empty()) sc = (m_.vdens[m_.V[3 * f]] + m_.vdens[m_.V[3 * f + 1]] + m_.vdens[m_.V[3 * f + 2]]) / 3.0;
        if (prm_.densityPower != 1.0) sc = std::pow(sc, prm_.densityPower);
        faceScale[f] = sc;
        target[f] = {U / (rho * sc), Vd / (rho * sc)};
        out.U[f] = U; out.V[f] = Vd;
        const Vec3 p0 = m_.P[m_.V[3 * f]], p1 = m_.P[m_.V[3 * f + 1]], p2 = m_.P[m_.V[3 * f + 2]];
        const Vec3 nn = cross(p1 - p0, p2 - p0);
        const double A2 = length(nn);                    // twice the area
        if (!(A2 > 1e-12)) continue;                     // a zero-area face carries no information (and would give NaN)
        const double A = 0.5 * A2;
        const Vec3 n = nn / A2;
        const Vec3 g[3] = {cross(n, p2 - p1) / A2, cross(n, p0 - p2) / A2, cross(n, p1 - p0) / A2};
        for (int a = 0; a < 3; ++a) {
            const int sa = sheetOf[3 * f + a];
            for (int b = 0; b < 3; ++b) {
                const int sb = sheetOf[3 * f + b];
                const double q = A * dot(g[a], g[b]);
                trip.push_back({static_cast<double>(2 * sa), static_cast<double>(2 * sb), q});
                trip.push_back({static_cast<double>(2 * sa + 1), static_cast<double>(2 * sb + 1), q});
            }
            rhs[2 * sa] += A * dot(g[a], target[f][0]);
            rhs[2 * sa + 1] += A * dot(g[a], target[f][1]);
        }
    }
    if (std::getenv("FORGE_DBG")) {
        int degen = 0, nanN = 0, nanTh = 0;
        for (int f = 0; f < nF; ++f) {
            if (!m_.falive[f]) continue;
            const Vec3 p0 = m_.P[m_.V[3 * f]], p1 = m_.P[m_.V[3 * f + 1]], p2 = m_.P[m_.V[3 * f + 2]];
            if (length(cross(p1 - p0, p2 - p0)) < 1e-12) ++degen;
            if (!std::isfinite(fr_.n[f].x) || !std::isfinite(fr_.e1[f].x)) ++nanN;
            if (!std::isfinite(theta[f])) ++nanTh;
        }
        std::fprintf(stderr, "param: degenerate faces %d, nan frames %d, nan theta %d\n", degen, nanN, nanTh);
        int bad = 0, tot = 0, badAll = 0;
        for (int e = 0; e < nE; ++e) {
            const int c0 = ec0[e];
            const int f = c0 / 3, g = m_.O[c0] / 3;
            const Vec3 nn = normalized(fr_.n[f] + fr_.n[g]);
            const double th1 = theta[f] + gauge[f] * halfPi, th2 = theta[g] + gauge[g] * halfPi;
            const Vec3 Uf = fr_.e1[f] * std::cos(th1) + fr_.e2[f] * std::sin(th1);
            const Vec3 Ug = fr_.e1[g] * std::cos(th2) + fr_.e2[g] * std::sin(th2);
            const double cs = dot(Uf, Ug), sn = dot(Uf, cross(nn, Ug));
            const int kk = mod4(static_cast<int>(std::lround(std::atan2(sn, cs) / halfPi)));
            ++tot; if (kk != jump[e]) { ++badAll; if (cut[e]) ++bad; }
        }
        std::fprintf(stderr, "param: jump check: edges %d mismatch %d (on cut %d)\n", tot, badAll, bad);
    }
    // seams:  x_B - Rot(k) x_A - t = 0 at both end points of every cut edge
    const double W = std::getenv("PW") ? std::atof(std::getenv("PW")) : 30.0;
    struct Seam { int sa, sb, te, k; };
    std::vector<Seam> seams;
    for (int e = 0; e < nE; ++e) {
        if (!cut[e]) continue;
        const int c0 = ec0[e], d0 = m_.O[c0];
        const int te = nS + cutIndex[e];
        const int fa = sheetOf[CMesh::nxt(c0)], ga = sheetOf[CMesh::prv(d0)];     // end A: f side, g side
        const int fb = sheetOf[CMesh::prv(c0)], gb = sheetOf[CMesh::nxt(d0)];     // end B
        if (fLeft[e]) { seams.push_back({fa, ga, te, jump[e]}); seams.push_back({fb, gb, te, jump[e]}); }
        else { const int k = mod4(-jump[e]); seams.push_back({ga, fa, te, k}); seams.push_back({gb, fb, te, k}); }
    }
    auto rotM = [](int k, double M[2][2]) {
        const double c = (k == 0) ? 1 : (k == 2 ? -1 : 0), s = (k == 1) ? 1 : (k == 3 ? -1 : 0);
        M[0][0] = c; M[0][1] = -s; M[1][0] = s; M[1][1] = c;
    };
    for (const Seam& s : seams) {
        // residual r = I x_B - Rot x_A - I t ; add W * r^T r
        double Rm[2][2];
        rotM(s.k, Rm);
        struct Term { int var; double M[2][2]; };
        Term terms[3];
        terms[0] = {s.sb, {{1, 0}, {0, 1}}};
        terms[1] = {s.sa, {{-Rm[0][0], -Rm[0][1]}, {-Rm[1][0], -Rm[1][1]}}};
        terms[2] = {s.te, {{-1, 0}, {0, -1}}};
        for (const Term& A : terms)
            for (const Term& B : terms)
                for (int i = 0; i < 2; ++i)
                    for (int j = 0; j < 2; ++j) {
                        double v = 0;
                        for (int r = 0; r < 2; ++r) v += A.M[r][i] * B.M[r][j];
                        if (v != 0) tripS.push_back({static_cast<double>(2 * A.var + i), static_cast<double>(2 * B.var + j), v});
                    }
    }
    // feature edges run along one of the two lattice directions: the other coordinate is constant along them
    std::vector<std::array<double, 3>> tripF;
    std::vector<int> fpar(nVar);
    std::iota(fpar.begin(), fpar.end(), 0);
    auto ffind = [&](int a) { while (fpar[a] != a) { fpar[a] = fpar[fpar[a]]; a = fpar[a]; } return a; };
    std::vector<int> featVars;
    if (prm_.alignFeatures) {
        for (int f = 0; f < nF; ++f) {
            if (!m_.falive[f]) continue;
            for (int k = 0; k < 3; ++k) {
                const int c = 3 * f + k;
                if (!m_.E[c]) continue;
                const int ka = CMesh::nxt(c), kb = CMesh::prv(c);
                const int sa = sheetOf[ka], sb = sheetOf[kb];
                const Vec3 d = m_.P[m_.V[kb]] - m_.P[m_.V[ka]];
                const int coord = std::fabs(dot(d, out.U[f])) >= std::fabs(dot(d, out.V[f])) ? 1 : 0;
                const int i = 2 * sa + coord, j = 2 * sb + coord;
                if (i == j) continue;
                tripF.push_back({static_cast<double>(i), static_cast<double>(i), 1.0});
                tripF.push_back({static_cast<double>(j), static_cast<double>(j), 1.0});
                tripF.push_back({static_cast<double>(i), static_cast<double>(j), -1.0});
                tripF.push_back({static_cast<double>(j), static_cast<double>(i), -1.0});
                fpar[ffind(i)] = ffind(j);
                featVars.push_back(i); featVars.push_back(j);
            }
        }
    }
    const double WF = 300.0;
    Csr K;
    Amg amg;
    bool useAmg = std::getenv("FORGE_AMG") != nullptr;  // experimental: slower than Jacobi-CG on current meshes
    std::vector<double> diag(nVar, 1.0);
    auto buildK = [&](double Wt) {
        std::vector<std::array<double, 3>> all = trip;
        all.reserve(trip.size() + tripS.size() + tripF.size());
        for (const auto& t : tripS) all.push_back({t[0], t[1], Wt * t[2]});
        for (const auto& t : tripF) all.push_back({t[0], t[1], WF * t[2]});
        K.build(nVar, all);
        for (int r = 0; r < nVar; ++r)
            for (int k = K.start[r]; k < K.start[r + 1]; ++k) if (K.col[k] == r) diag[r] = std::max(K.val[k], 1e-12);
        if (useAmg) {
            Csr Kr = K;
            for (int r = 0; r < nVar; ++r)
                for (int k = Kr.start[r]; k < Kr.start[r + 1]; ++k) if (Kr.col[k] == r) Kr.val[k] += 1e-5 * diag[r];
            std::vector<std::array<double, 3>> gt = trip;
            for (const auto& t : tripF) gt.push_back({t[0], t[1], WF * t[2]});
            Csr G;
            G.build(nVar, gt);
            for (int r = 0; r < nVar; ++r) {
                double dg = 0;
                for (int k = G.start[r]; k < G.start[r + 1]; ++k) if (G.col[k] == r) dg = G.val[k];
                for (int k = G.start[r]; k < G.start[r + 1]; ++k) if (G.col[k] == r) G.val[k] += 1e-5 * std::max(dg, 1e-12);
            }
            amg.build(Kr, G, 2);
            if (std::getenv("FORGE_AMGTEST")) {
                std::vector<double> bb(nVar), xx(nVar, 0.0), rr(nVar), zz;
                for (int i = 0; i < nVar; ++i) bb[i] = std::sin(0.37 * i) + 0.3 * std::cos(1.7 * i);
                for (int it = 0; it < 8; ++it) {
                    Kr.mul(xx, rr);
                    double nr = 0;
                    for (int i = 0; i < nVar; ++i) { rr[i] = bb[i] - rr[i]; nr += rr[i] * rr[i]; }
                    std::fprintf(stderr, "amgtest it %d res %.3e\n", it, std::sqrt(nr));
                    amg.apply(rr, zz);
                    for (int i = 0; i < nVar; ++i) xx[i] += zz[i];
                }
            }
            if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: amg levels %d coarse %d (n %d)\n", amg.numLevels(), amg.coarseRows(), nVar);
        }
    };
    buildK(W);

    out.uv.assign(nF, {});
    lap("assembled");
    // ---- solve and round ----
    std::vector<double> x(nVar, 0.0);
    std::vector<uint8_t> fixedMask(nVar, 0);
    auto diagnose = [&]() {
    for (int f = 0; f < nF; ++f) { if (!m_.falive[f]) continue; for (int k = 0; k < 3; ++k) { const int s = sheetOf[3 * f + k]; out.uv[f][k] = {x[2 * s], x[2 * s + 1]}; } }
    double fe = 0, fa = 0;
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        const Vec3 p0 = m_.P[m_.V[3 * f]], p1 = m_.P[m_.V[3 * f + 1]], p2 = m_.P[m_.V[3 * f + 2]];
        const Vec3 nn = cross(p1 - p0, p2 - p0);
        const double A2 = length(nn), n_ = A2;
        if (!(A2 > 1e-12)) continue;
        const Vec3 n = nn / n_;
        const Vec3 g[3] = {cross(n, p2 - p1) / A2, cross(n, p0 - p2) / A2, cross(n, p1 - p0) / A2};
        Vec3 gu, gv;
        for (int a = 0; a < 3; ++a) { gu += g[a] * out.uv[f][a][0]; gv += g[a] * out.uv[f][a][1]; }
        const double e = std::sqrt(length2(gu - target[f][0]) + length2(gv - target[f][1])) * rho * faceScale[f];
        fe += e * 0.5 * A2; fa += 0.5 * A2;
    }
    out.faceError = fa > 0 ? fe / fa : 0;
    out.seamError = 0; out.badSeams = 0;
    for (const Seam& s : seams) {
        double Rm[2][2];
        rotM(s.k, Rm);
        const double ru = x[2 * s.sb] - (Rm[0][0] * x[2 * s.sa] + Rm[0][1] * x[2 * s.sa + 1]) - x[2 * s.te];
        const double rv = x[2 * s.sb + 1] - (Rm[1][0] * x[2 * s.sa] + Rm[1][1] * x[2 * s.sa + 1]) - x[2 * s.te + 1];
        out.seamError = std::max(out.seamError, std::sqrt(ru * ru + rv * rv));
        if (ru * ru + rv * rv > 0.0625) ++out.badSeams;
    }
    };
    // initial guess: face-local values from the vertex positions along the field (a few CG iterations fix the rest)
    auto doSolve = [&](int maxIter, double tol = 1e-5) { out.solves++; return solve(K, rhs, x, fixedMask, diag, maxIter, tol, useAmg ? &amg : nullptr); };
    // pin the global translation of every connected component
    for (int rf : dualRoots) {
        const int pin = sheetOf[3 * rf];
        fixedMask[2 * pin] = fixedMask[2 * pin + 1] = 1;
        x[2 * pin] = x[2 * pin + 1] = 0.0;
    }
    if (out.warm.size() == x.size() && out.warmRho > 0) {
        for (size_t i = 0; i < x.size(); ++i) if (!fixedMask[i]) x[i] = out.warm[i] * (out.warmRho / rho);
    }
    { int it = doSolve(6000, 1e-5); if (std::getenv("FORGE_DBG")) { diagnose(); std::fprintf(stderr, "param: real solve iters %d faceErr %.4f seamErr %.3e\n", it, out.faceError, out.seamError); } }
    lap("real solve");
    if (timeUp()) { out.error = "time limit"; return false; }
    std::vector<double> realX;
    realX = x;
    out.warm = x; out.warmRho = rho;
    auto roundSet = [&](std::vector<int> vars) {
        std::sort(vars.begin(), vars.end());
        vars.erase(std::unique(vars.begin(), vars.end()), vars.end());
        std::vector<int> open;
        for (int v : vars) if (!fixedMask[v]) open.push_back(v);
        while (!open.empty()) {
            std::vector<std::pair<double, int>> dist;
            dist.reserve(open.size());
            for (int v : open) dist.push_back({std::fabs(x[v] - std::round(x[v])), v});
            std::sort(dist.begin(), dist.end());
            size_t take = std::max<size_t>(1, open.size() / 3);
            size_t cnt = 0;
            while (cnt < dist.size() && (cnt < take || dist[cnt].first < 0.02)) ++cnt;
            for (size_t i = 0; i < cnt; ++i) { const int v = dist[i].second; x[v] = std::round(x[v]); fixedMask[v] = 1; }
            open.clear();
            for (size_t i = cnt; i < dist.size(); ++i) open.push_back(dist[i].second);
            doSolve(300, 1e-3);
        }
    };
    {   // 1. the singular vertices sit on lattice nodes; the translations around them follow
        std::vector<int> sv;
        for (int v : out.singular) { const int s = sheetOf[m_.vcorner[v]]; sv.push_back(2 * s); sv.push_back(2 * s + 1); }
        roundSet(sv);
        // paths that end at a singular vertex have a translation fixed by the lattice node there
        for (const Seam& sm : seams) {
            if (sm.sa == sm.sb && fixedMask[2 * sm.sa] && !fixedMask[2 * sm.te]) {
                double Rm[2][2]; rotM(sm.k, Rm);
                x[2 * sm.te] = std::round(x[2 * sm.sb] - (Rm[0][0] * x[2 * sm.sa] + Rm[0][1] * x[2 * sm.sa + 1]));
                x[2 * sm.te + 1] = std::round(x[2 * sm.sb + 1] - (Rm[1][0] * x[2 * sm.sa] + Rm[1][1] * x[2 * sm.sa + 1]));
                fixedMask[2 * sm.te] = fixedMask[2 * sm.te + 1] = 1;
            }
        }
        lap("singular");
        if (timeUp()) { out.error = "time limit"; return false; }
        buildK(1000.0);
        doSolve(3000, 1e-6);
        lap("stiff");
        if (timeUp()) { out.error = "time limit"; return false; }
        if (std::getenv("FORGE_DBG")) {
            int nearC = 0, farC = 0, fixedC = 0; double worst = 0;
            for (int sg = 0; sg < nCut; ++sg) {
                const int a = 2 * (nS + sg);
                if (fixedMask[a]) { ++fixedC; continue; }
                const double d = std::max(std::fabs(x[a] - std::round(x[a])), std::fabs(x[a + 1] - std::round(x[a + 1])));
                if (d < 0.06) ++nearC; else ++farC;
                worst = std::max(worst, d);
            }
            std::fprintf(stderr, "param: segments %d fixed %d near %d far %d worst %.2f\n", nCut, fixedC, nearC, farC, worst);
        }
        if (std::getenv("FORGE_DBG")) {
            int nfree = 0; std::vector<double> ds;
            for (int sg = 0; sg < nCut; ++sg) { const int a = 2 * (nS + sg); if (fixedMask[a]) continue; ++nfree; ds.push_back(std::max(std::fabs(x[a] - std::round(x[a])), std::fabs(x[a + 1] - std::round(x[a + 1])))); }
            std::sort(ds.begin(), ds.end());
            std::fprintf(stderr, "param: free path translations %d; distance to integer:", nfree);
            for (double d : ds) std::fprintf(stderr, " %.2f", d);
            std::fprintf(stderr, "\n");
        }
        // feature lines: every group of constrained coordinates takes one integer value
        if (!featVars.empty()) {
            std::sort(featVars.begin(), featVars.end());
            featVars.erase(std::unique(featVars.begin(), featVars.end()), featVars.end());
            std::unordered_map<int, int> gid;
            std::vector<std::vector<int>> groups;
            for (int v : featVars) {
                const int r = ffind(v);
                auto it = gid.find(r);
                if (it == gid.end()) { it = gid.emplace(r, static_cast<int>(groups.size())).first; groups.emplace_back(); }
                groups[it->second].push_back(v);
            }
            std::vector<int> openG;
            for (int g = 0; g < static_cast<int>(groups.size()); ++g) openG.push_back(g);
            int rounds = 0;
            while (!openG.empty() && rounds < 40) {
                ++rounds;
                std::vector<std::pair<double, int>> dist;
                std::vector<double> val(groups.size(), 0.0);
                for (int g : openG) {
                    double sum = 0; int n = 0; bool haveFixed = false; double fv = 0;
                    for (int v : groups[g]) { if (fixedMask[v]) { haveFixed = true; fv = x[v]; } sum += x[v]; ++n; }
                    val[g] = haveFixed ? fv : sum / n;
                    dist.push_back({haveFixed ? 0.0 : std::fabs(val[g] - std::round(val[g])), g});
                }
                std::sort(dist.begin(), dist.end());
                size_t take = std::max<size_t>(1, openG.size() / 4);
                size_t cnt = 0;
                while (cnt < dist.size() && (cnt < take || dist[cnt].first < 0.02)) ++cnt;
                for (size_t i = 0; i < cnt; ++i) {
                    const int g = dist[i].second;
                    const double r = std::round(val[g]);
                    for (int v : groups[g]) { x[v] = r; fixedMask[v] = 1; }
                }
                openG.clear();
                for (size_t i = cnt; i < dist.size(); ++i) openG.push_back(dist[i].second);
                doSolve(600, 1e-4);
            }
            lap("feature lines");
            if (timeUp()) { out.error = "time limit"; return false; }
        }
        // translations: one batch never holds two paths that meet at a junction, because the last
        // path of a junction is fixed by the others (the loop around the junction must close)
        std::vector<int> open;
        for (int sg = 0; sg < nCut; ++sg) if (!fixedMask[2 * (nS + sg)]) open.push_back(sg);
        std::vector<int> touchedAt(nVs, -1);
        int stamp = 0;
        while (!open.empty()) {
            std::vector<std::pair<double, int>> dist;
            for (int sg : open) {
                const int a = 2 * (nS + sg);
                dist.push_back({std::max(std::fabs(x[a] - std::round(x[a])), std::fabs(x[a + 1] - std::round(x[a + 1]))), sg});
            }
            std::sort(dist.begin(), dist.end());
            ++stamp;
            std::vector<int> rest;
            int taken = 0;
            for (auto& dsg : dist) {
                const int sg = dsg.second;
                // a path whose value is already (nearly) integer is determined by the others: take it
                bool ok = dsg.first < 0.06;
                if (!ok) {   // a free choice: one per junction in each batch, the others follow from it
                    ok = true;
                    for (int v : segJunction[sg]) if (touchedAt[v] == stamp) { ok = false; break; }
                    if (ok) for (int v : segJunction[sg]) touchedAt[v] = stamp;
                }
                if (!ok) { rest.push_back(sg); continue; }
                ++taken;
                const int a = 2 * (nS + sg);
                x[a] = std::round(x[a]); x[a + 1] = std::round(x[a + 1]);
                fixedMask[a] = fixedMask[a + 1] = 1;
            }
            open.swap(rest);
            doSolve(std::getenv("FORGE_RIT") ? std::atoi(std::getenv("FORGE_RIT")) : 600, std::getenv("FORGE_RTOL") ? std::atof(std::getenv("FORGE_RTOL")) : 1e-4);
        }
    }
    lap("rounded");
    buildK(1000.0);
    doSolve(3000, 1e-6);
    lap("final");

    // A junction of three cut paths that are not singular needs its integer translations to add up to zero.
    // The batch rounding can miss that around two neighbouring junctions that share a path; the leftover is a
    // residual of about 1/3 on every seam of those junctions. Shifting the shared path by one cell repairs both.
    {
        auto seamRes = [&](size_t i, double& ru, double& rv) {
            const Seam& s = seams[i];
            double Rm[2][2]; rotM(s.k, Rm);
            ru = x[2 * s.sb] - (Rm[0][0] * x[2 * s.sa] + Rm[0][1] * x[2 * s.sa + 1]) - x[2 * s.te];
            rv = x[2 * s.sb + 1] - (Rm[1][0] * x[2 * s.sa] + Rm[1][1] * x[2 * s.sa + 1]) - x[2 * s.te + 1];
        };
        // score: number of bad seams first, total squared residual second
        auto score = [&]() { double bad = 0, sq = 0; for (size_t i = 0; i < seams.size(); ++i) { double a, b; seamRes(i, a, b); const double r2 = a * a + b * b; sq += r2; if (r2 > 0.0625) bad += 1; } return bad * 1e6 + sq; };
        double cur = score();
        if (cur >= 1e6 && std::getenv("FORGE_SEAMFIX")) {   // experimental, off by default (see docs/STATUS_AND_LIMITS.md)
            int tries = 0;
            bool improved = true;
            while (improved && cur >= 1e6 && tries < 24 && !timeUp()) {
                improved = false;
                std::unordered_map<int, int> count;
                std::unordered_map<int, std::array<double, 2>> firstRes;
                for (size_t i = 0; i < seams.size(); ++i) {
                    double a, b; seamRes(i, a, b);
                    if (a * a + b * b > 0.0625) { count[seams[i].te]++; if (!firstRes.count(seams[i].te)) firstRes[seams[i].te] = {a, b}; }
                }
                std::vector<std::pair<int, int>> cand;
                for (const auto& kv : count) cand.push_back({-kv.second, kv.first});
                std::sort(cand.begin(), cand.end());
                {   // poles at the leaves of the cut tree that contains the bad junctions, nearest first
                    std::unordered_map<int, std::vector<int>> segsAt;
                    for (int sg = 0; sg < nCut; ++sg) for (int v : segJunction[sg]) segsAt[v].push_back(sg);
                    std::unordered_map<int, int> dist;
                    std::vector<int> queue;
                    for (const auto& kv : count) { const int sg = kv.first - nS; if (sg >= 0 && sg < nCut) { dist[sg] = 0; queue.push_back(sg); } }
                    std::vector<std::pair<int, int>> poles;      // (distance, segment variable)
                    for (size_t qi = 0; qi < queue.size(); ++qi) {
                        const int sg = queue[qi];
                        for (int v : segJunction[sg]) for (int o : segsAt[v]) if (!dist.count(o)) { dist[o] = dist[sg] + 1; queue.push_back(o); }
                    }
                    for (const auto& kv : dist) {
                        const int te2 = nS + kv.first;
                        for (const Seam& sm : seams) if (sm.te == te2 && sm.sa == sm.sb && fixedMask[2 * sm.sa]) { poles.push_back({kv.second, te2}); break; }
                    }
                    std::sort(poles.begin(), poles.end());
                    cand.clear();
                    for (const auto& p : poles) cand.push_back({p.first, p.second});
                    if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: seam repair: %zu pole paths in the tree of the bad junctions\n", poles.size());
                }
                for (const auto& cd : cand) {
                    if (tries >= 24 || timeUp()) break;
                    const int te = cd.second;
                    const int a0 = 2 * te;
                    const std::vector<double> snap = x;
                    // the leftover on a seam is about a third of the junction's integer defect: try that shift first
                    std::vector<std::array<double, 2>> dirs;
                    const auto fr = firstRes.count(te) ? firstRes[te] : std::array<double, 2>{0, 0};
                    const double pu = std::round(3 * fr[0]), pv = std::round(3 * fr[1]);
                    if (pu != 0 || pv != 0) { dirs.push_back({pu, pv}); dirs.push_back({-pu, -pv}); }
                    const double extra[8][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}, {1, 1}, {1, -1}, {-1, 1}, {-1, -1}};
                    for (const auto& e8 : extra) {
                        bool dup = false;
                        for (const auto& d0 : dirs) if (d0[0] == e8[0] && d0[1] == e8[1]) dup = true;
                        if (!dup) dirs.push_back({e8[0], e8[1]});
                    }
                    // a path that ends at a pole (leaf of the cut graph): move the pole one lattice step and the path with it
                    int poleSheet = -1, poleK = 0;
                    for (const Seam& sm : seams) if (sm.te == te && sm.sa == sm.sb && fixedMask[2 * sm.sa]) { poleSheet = sm.sa; poleK = sm.k; break; }
                    if (poleSheet >= 0) {
                        double Rm[2][2]; rotM(poleK, Rm);
                        const double units[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
                        dirs.clear();
                        for (const auto& u : units) dirs.push_back({u[0], u[1]});
                        for (size_t di = 0; di < dirs.size() && tries < 24; ++di) {
                            const double eu = dirs[di][0], ev = dirs[di][1];
                            x = snap;
                            x[2 * poleSheet] += eu; x[2 * poleSheet + 1] += ev;
                            x[a0] += eu - (Rm[0][0] * eu + Rm[0][1] * ev);
                            x[a0 + 1] += ev - (Rm[1][0] * eu + Rm[1][1] * ev);
                            doSolve(1500, 1e-6);
                            ++tries;
                            const double now = score();
                            if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: seam repair pole of path %d step (%.0f,%.0f): bad seams %d -> %d\n", te, eu, ev, static_cast<int>(cur / 1e6), static_cast<int>(now / 1e6));
                            if (now < cur - 1e-6) { cur = now; improved = true; break; }
                        }
                        if (improved) break;
                        x = snap;
                        continue;
                    }
                    for (const auto& dv2 : dirs) {
                        if (tries >= 24) break;
                        const double du = dv2[0], dv = dv2[1];
                        x = snap;
                        x[a0] += du; x[a0 + 1] += dv;
                        doSolve(1500, 1e-6);
                        ++tries;
                        const double now = score();
                        if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: seam repair path %d shift (%.0f,%.0f): bad seams %d -> %d\n", te, du, dv, static_cast<int>(cur / 1e6), static_cast<int>(now / 1e6));
                        if (now < cur - 1e-6) { cur = now; improved = true; break; }
                    }
                    if (improved) break;
                    x = snap;
                }
            }
        }
    }

    // ---- results and diagnostics ----
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        for (int k = 0; k < 3; ++k) {
            const int s = sheetOf[3 * f + k];
            out.uv[f][k] = {x[2 * s], x[2 * s + 1]};
        }
    }
    diagnose();
    if (std::getenv("FORGE_DBG")) {
        int flipped = 0, squashed = 0, stretched = 0; int shownF = 0;
        for (int f = 0; f < nF; ++f) {
            if (!m_.falive[f]) continue;
            const Vec3 p0 = m_.P[m_.V[3 * f]], p1 = m_.P[m_.V[3 * f + 1]], p2 = m_.P[m_.V[3 * f + 2]];
            const double a3 = 0.5 * length(cross(p1 - p0, p2 - p0));
            if (!(a3 > 1e-12)) continue;
            const auto& uv = out.uv[f];
            const double auv = 0.5 * ((uv[1][0] - uv[0][0]) * (uv[2][1] - uv[0][1]) - (uv[1][1] - uv[0][1]) * (uv[2][0] - uv[0][0]));
            double sc = 1.0; if (!m_.vdens.empty()) sc = (m_.vdens[m_.V[3 * f]] + m_.vdens[m_.V[3 * f + 1]] + m_.vdens[m_.V[3 * f + 2]]) / 3.0;
            const double expect = a3 / (rho * rho * sc * sc);
            const double r = auv / expect;
            if (r < 0) { ++flipped; if (shownF++ < 12) { const Vec3 c = (p0 + p1 + p2) / 3.0; std::fprintf(stderr, "param: flipped face at (%.1f,%.1f,%.1f) ratio %.2f\n", c.x, c.y, c.z, r); } }
            else if (r < 0.3) ++squashed;
            else if (r > 3) { ++stretched; if (stretched <= 14) { const Vec3 c = (p0 + p1 + p2) / 3.0; std::fprintf(stderr, "param: stretched face at (%.1f,%.1f,%.1f) ratio %.1f\n", c.x, c.y, c.z, r); } }
        }
        std::fprintf(stderr, "param: uv area ratio: flipped %d squashed(<0.3) %d stretched(>3) %d\n", flipped, squashed, stretched);
    }
    if (std::getenv("FORGE_DBG")) {
        int shown = 0;
        for (size_t i = 0; i < seams.size() && shown < 6; ++i) {
            const Seam& s = seams[i];
            double Rm[2][2]; rotM(s.k, Rm);
            const double ru = x[2 * s.sb] - (Rm[0][0] * x[2 * s.sa] + Rm[0][1] * x[2 * s.sa + 1]) - x[2 * s.te];
            const double rv = x[2 * s.sb + 1] - (Rm[1][0] * x[2 * s.sa] + Rm[1][1] * x[2 * s.sa + 1]) - x[2 * s.te + 1];
            if (std::sqrt(ru * ru + rv * rv) > 0.3) {
                ++shown;
                {   int cnt = 0, ee = -1;
                    for (int e = 0; e < nE; ++e) if (cut[e]) { if (cnt == static_cast<int>(i) / 2) { ee = e; break; } ++cnt; }
                    if (ee >= 0) {
                        const Vec3 pa = m_.P[endA(ee)], pb = m_.P[endB(ee)];
                        std::fprintf(stderr, "param: bad seam edge %d jump %d endA %d (sing %d) at (%.1f,%.1f,%.1f) endB %d (sing %d) at (%.1f,%.1f,%.1f) cutIndex %d\n", ee, jump[ee], endA(ee), (int)isSing[endA(ee)], pa.x, pa.y, pa.z, endB(ee), (int)isSing[endB(ee)], pb.x, pb.y, pb.z, cutIndex[ee]);
                    }
                }
                std::fprintf(stderr, "param: bad seam %zu k %d res (%.2f,%.2f) xa (%.2f,%.2f) xb (%.2f,%.2f) t (%.2f,%.2f) fixedA %d fixedB %d fixedT %d\n", i, s.k, ru, rv, x[2*s.sa], x[2*s.sa+1], x[2*s.sb], x[2*s.sb+1], x[2*s.te], x[2*s.te+1], fixedMask[2*s.sa], fixedMask[2*s.sb], fixedMask[2*s.te]);
            }
        }
    }
    return true;
}

}  // namespace forge
