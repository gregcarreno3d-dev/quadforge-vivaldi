// Forge engine - binary mesh exchange format (.fmesh), little endian.
//
//   char[4]  "FMSH"
//   u32      version (1)
//   u32      nV, nPoly, nIdx, nFeat, flags
//   f32      positions[3*nV]
//   i32      polygon sizes[nPoly]
//   i32      polygon vertex indices[nIdx]
//   i32      feature edges[2*nFeat]            vertex index pairs that must be kept as lines
//   f32      density[nV]                       only if flags & 1 (edge length multiplier)
//   f32      field[3*nV]                       only if flags & 2 (unused, reserved)
//   u32 nGuide, f32 guides[6*nGuide]          only if flags & 4 (flow guide segments: x0 y0 z0 x1 y1 z1)
#pragma once
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#include "vec.hpp"

namespace forge {

struct PolyMesh {
    std::vector<Vec3> P;
    std::vector<int> sizes;
    std::vector<int> idx;
    std::vector<std::array<int, 2>> features;
    std::vector<float> density;  // empty when not given
    std::vector<std::array<Vec3, 2>> guides;   // flow guide segments (3D end points), soft hints for the quad rows
};

namespace detail {
template <class T>
void readArray(FILE* f, std::vector<T>& out, size_t n, const char* what) {
    out.resize(n);
    if (n && std::fread(out.data(), sizeof(T), n, f) != n)
        throw std::runtime_error(std::string("truncated file while reading ") + what);
}
}  // namespace detail

inline PolyMesh readFmesh(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) throw std::runtime_error("cannot open input file: " + path);
    struct Closer { FILE* f; ~Closer() { std::fclose(f); } } closer{f};
    char magic[4];
    uint32_t hdr[6];
    if (std::fread(magic, 1, 4, f) != 4 || std::memcmp(magic, "FMSH", 4) != 0)
        throw std::runtime_error("not an FMSH file");
    if (std::fread(hdr, 4, 6, f) != 6) throw std::runtime_error("truncated header");
    if (hdr[0] != 1) throw std::runtime_error("unsupported FMSH version");
    const size_t nV = hdr[1], nPoly = hdr[2], nIdx = hdr[3], nFeat = hdr[4];
    const uint32_t flags = hdr[5];
    PolyMesh m;
    std::vector<float> pos;
    detail::readArray(f, pos, nV * 3, "positions");
    m.P.resize(nV);
    for (size_t i = 0; i < nV; ++i) m.P[i] = Vec3(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]);
    detail::readArray(f, m.sizes, nPoly, "polygon sizes");
    detail::readArray(f, m.idx, nIdx, "polygon indices");
    std::vector<int> feat;
    detail::readArray(f, feat, nFeat * 2, "feature edges");
    m.features.resize(nFeat);
    for (size_t i = 0; i < nFeat; ++i) m.features[i] = {feat[2 * i], feat[2 * i + 1]};
    if (flags & 1u) detail::readArray(f, m.density, nV, "density");
    if (flags & 4u) {
        uint32_t ng = 0;
        if (std::fread(&ng, 4, 1, f) != 1) throw std::runtime_error("truncated file while reading guides");
        std::vector<float> g;
        detail::readArray(f, g, static_cast<size_t>(ng) * 6, "guides");
        m.guides.resize(ng);
        for (size_t i = 0; i < ng; ++i)
            m.guides[i] = {Vec3(g[6 * i], g[6 * i + 1], g[6 * i + 2]), Vec3(g[6 * i + 3], g[6 * i + 4], g[6 * i + 5])};
    }
    // validate indices
    size_t total = 0;
    for (int s : m.sizes) {
        if (s < 3) throw std::runtime_error("polygon with fewer than 3 vertices");
        total += static_cast<size_t>(s);
    }
    if (total != nIdx) throw std::runtime_error("polygon sizes do not match index count");
    for (int i : m.idx)
        if (i < 0 || static_cast<size_t>(i) >= nV) throw std::runtime_error("vertex index out of range");
    for (auto& e : m.features)
        if (e[0] < 0 || e[1] < 0 || static_cast<size_t>(e[0]) >= nV || static_cast<size_t>(e[1]) >= nV)
            throw std::runtime_error("feature edge index out of range");
    return m;
}

inline void writeFmesh(const std::string& path, const PolyMesh& m) {
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) throw std::runtime_error("cannot open output file: " + path);
    struct Closer { FILE* f; ~Closer() { std::fclose(f); } } closer{f};
    const uint32_t flags = m.density.empty() ? 0u : 1u;
    const uint32_t hdr[6] = {1u, static_cast<uint32_t>(m.P.size()), static_cast<uint32_t>(m.sizes.size()),
                             static_cast<uint32_t>(m.idx.size()), static_cast<uint32_t>(m.features.size()), flags};
    std::fwrite("FMSH", 1, 4, f);
    std::fwrite(hdr, 4, 6, f);
    std::vector<float> pos(m.P.size() * 3);
    for (size_t i = 0; i < m.P.size(); ++i) {
        pos[3 * i] = static_cast<float>(m.P[i].x);
        pos[3 * i + 1] = static_cast<float>(m.P[i].y);
        pos[3 * i + 2] = static_cast<float>(m.P[i].z);
    }
    if (!pos.empty()) std::fwrite(pos.data(), 4, pos.size(), f);
    if (!m.sizes.empty()) std::fwrite(m.sizes.data(), 4, m.sizes.size(), f);
    if (!m.idx.empty()) std::fwrite(m.idx.data(), 4, m.idx.size(), f);
    for (auto& e : m.features) std::fwrite(e.data(), 4, 2, f);
    if (flags & 1u) std::fwrite(m.density.data(), 4, m.density.size(), f);
}

}  // namespace forge
