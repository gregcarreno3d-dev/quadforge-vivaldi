// Forge engine - bounding volume hierarchy for closest-point queries.
#pragma once
#include <array>
#include <cstdint>
#include <numeric>
#include <vector>

#include "vec.hpp"

namespace forge {

// Generic BVH over primitives given as boxes. The caller supplies an evaluator
// that computes the squared distance to one primitive and records the hit.
class BVH {
public:
    struct Node {
        AABB box;
        int left = -1, right = -1;   // children (inner node)
        int first = 0, count = 0;    // range in order_ (leaf when count > 0)
    };

    void build(const std::vector<AABB>& boxes) {
        const int n = static_cast<int>(boxes.size());
        order_.resize(n);
        std::iota(order_.begin(), order_.end(), 0);
        nodes_.clear();
        if (n == 0) return;
        nodes_.reserve(2 * n / kLeaf + 8);
        std::vector<Vec3> centers(n);
        for (int i = 0; i < n; ++i) centers[i] = boxes[i].center();

        struct Item { int node, begin, end; };
        std::vector<Item> stack;
        nodes_.emplace_back();
        stack.push_back({0, 0, n});
        while (!stack.empty()) {
            Item it = stack.back();
            stack.pop_back();
            AABB box, cbox;
            for (int i = it.begin; i < it.end; ++i) {
                box.expand(boxes[order_[i]]);
                cbox.expand(centers[order_[i]]);
            }
            nodes_[it.node].box = box;
            const int count = it.end - it.begin;
            const Vec3 ext = cbox.extent();
            int axis = 0;
            if (ext.y > ext.x) axis = 1;
            if (ext.z > ext[axis]) axis = 2;
            if (count <= kLeaf || ext[axis] <= 0.0) {
                nodes_[it.node].first = it.begin;
                nodes_[it.node].count = count;
                continue;
            }
            const int mid = it.begin + count / 2;
            std::nth_element(order_.begin() + it.begin, order_.begin() + mid, order_.begin() + it.end,
                             [&](int a, int b) { return centers[a][axis] < centers[b][axis]; });
            const int l = static_cast<int>(nodes_.size());
            nodes_.emplace_back();
            nodes_.emplace_back();
            nodes_[it.node].left = l;
            nodes_[it.node].right = l + 1;
            stack.push_back({l, it.begin, mid});
            stack.push_back({l + 1, mid, it.end});
        }
    }

    bool empty() const { return nodes_.empty(); }

    // eval(primitive_index, best_d2) must return the squared distance if it is
    // smaller than best_d2 (recording the hit itself), otherwise anything >= best_d2.
    template <class Eval>
    void closest(const Vec3& p, double& best2, Eval&& eval) const {
        if (nodes_.empty()) return;
        int stack[96];
        double sd[96];
        int sp = 0;
        stack[sp] = 0;
        sd[sp++] = nodes_[0].box.distance2To(p);
        while (sp > 0) {
            --sp;
            if (sd[sp] >= best2) continue;
            const Node& nd = nodes_[stack[sp]];
            if (nd.count > 0) {
                for (int i = nd.first; i < nd.first + nd.count; ++i) {
                    const double d2 = eval(order_[i], best2);
                    if (d2 < best2) best2 = d2;
                }
            } else {
                const double dl = nodes_[nd.left].box.distance2To(p);
                const double dr = nodes_[nd.right].box.distance2To(p);
                // push the farther child first so the nearer one is processed first
                if (dl < dr) {
                    if (dr < best2) { stack[sp] = nd.right; sd[sp++] = dr; }
                    if (dl < best2) { stack[sp] = nd.left; sd[sp++] = dl; }
                } else {
                    if (dl < best2) { stack[sp] = nd.left; sd[sp++] = dl; }
                    if (dr < best2) { stack[sp] = nd.right; sd[sp++] = dr; }
                }
            }
        }
    }

private:
    static constexpr int kLeaf = 4;
    std::vector<Node> nodes_;
    std::vector<int> order_;
};

// Triangle surface with closest-point queries (the "reference" the remesh snaps onto).
class SurfaceIndex {
public:
    struct Hit {
        double d2 = kInf;
        int tri = -1;
        double u = 0, v = 0;  // barycentric weights of b and c
        Vec3 p;
    };

    void build(const std::vector<Vec3>& P, const std::vector<std::array<int, 3>>& T) {
        P_ = &P;
        T_ = &T;
        normals_.resize(T.size());
        std::vector<AABB> boxes(T.size());
        for (size_t i = 0; i < T.size(); ++i) {
            const auto& t = T[i];
            AABB b;
            b.expand(P[t[0]]); b.expand(P[t[1]]); b.expand(P[t[2]]);
            boxes[i] = b;
            normals_[i] = normalized(triangleNormalUnnormalized(P[t[0]], P[t[1]], P[t[2]]));
        }
        bvh_.build(boxes);
    }

    // Closest point on the surface. When `normal` is given, triangles that face
    // away from it (dot < minDot) are skipped, which stops thin shells from
    // grabbing points that belong to the other side.
    Hit closest(const Vec3& q, const Vec3* normal = nullptr, double minDot = -2.0) const {
        Hit hit = query(q, normal, minDot);
        if (hit.tri < 0 && normal) hit = query(q, nullptr, -2.0);
        return hit;
    }

    const Vec3& faceNormal(int tri) const { return normals_[tri]; }
    bool empty() const { return bvh_.empty(); }

private:
    Hit query(const Vec3& q, const Vec3* normal, double minDot) const {
        Hit best;
        double best2 = kInf;
        const auto& P = *P_;
        const auto& T = *T_;
        bvh_.closest(q, best2, [&](int i, double bound) -> double {
            if (normal && dot(normals_[i], *normal) < minDot) return kInf;
            const auto& t = T[i];
            double u, v;
            const Vec3 c = closestPointOnTriangle(q, P[t[0]], P[t[1]], P[t[2]], u, v);
            const double d2 = distance2(c, q);
            if (d2 < bound) { best.d2 = d2; best.tri = i; best.u = u; best.v = v; best.p = c; }
            return d2;
        });
        return best;
    }

    const std::vector<Vec3>* P_ = nullptr;
    const std::vector<std::array<int, 3>>* T_ = nullptr;
    std::vector<Vec3> normals_;
    BVH bvh_;
};

// Polylines (feature edges, open borders) with closest-point queries.
class SegmentIndex {
public:
    struct Hit {
        double d2 = kInf;
        int seg = -1;
        double t = 0;
        Vec3 p;
    };

    void build(std::vector<std::array<Vec3, 2>> segments) {
        S_ = std::move(segments);
        std::vector<AABB> boxes(S_.size());
        for (size_t i = 0; i < S_.size(); ++i) {
            boxes[i].expand(S_[i][0]);
            boxes[i].expand(S_[i][1]);
        }
        bvh_.build(boxes);
    }

    Hit closest(const Vec3& q) const {
        Hit best;
        double best2 = kInf;
        bvh_.closest(q, best2, [&](int i, double bound) -> double {
            double t;
            const Vec3 c = closestPointOnSegment(q, S_[i][0], S_[i][1], t);
            const double d2 = distance2(c, q);
            if (d2 < bound) { best.d2 = d2; best.seg = i; best.t = t; best.p = c; }
            return d2;
        });
        return best;
    }

    bool empty() const { return S_.empty(); }
    const std::array<Vec3, 2>& segment(int i) const { return S_[i]; }

private:
    std::vector<std::array<Vec3, 2>> S_;
    BVH bvh_;
};

}  // namespace forge
