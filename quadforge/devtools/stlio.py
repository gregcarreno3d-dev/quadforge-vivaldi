"""Binary/ASCII STL reader that welds vertices (for engine experiments)."""
import struct
import numpy as np

def read_stl(path, weld=5):
    b = open(path, "rb").read()
    n = struct.unpack("<I", b[80:84])[0]
    if 84 + 50 * n == len(b):
        a = np.frombuffer(b[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        v = a["v"].reshape(-1, 3).astype(np.float64)
    else:
        v = np.array([[float(x) for x in l.split()[1:]] for l in b.decode().splitlines() if l.strip().startswith("vertex")])
    u, inv = np.unique(np.round(v, weld), axis=0, return_inverse=True)
    return u, inv.reshape(-1, 3)
