"""Flow guides: marked edges must pull the quad rows along them, with and without symmetry (needs the bpy module)."""
import sys,numpy as np
import pathlib
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE)); sys.path.insert(0,str(HERE.parent/'devtools'))
import blender_matrix_test as M
import meshgen as g
def aligned(mesh,guides,reach=0.12,tol=20):
    co=np.array([v.co[:] for v in mesh.vertices]);ok=tot=0
    for e in mesh.edges:
        a,b=co[e.vertices[0]],co[e.vertices[1]];m=(a+b)/2;ev=b-a;ev/=np.linalg.norm(ev)+1e-12
        best=(1e9,None)
        for s in guides:
            d=s[1]-s[0];L=np.linalg.norm(d);d=d/L;t=np.clip(np.dot(m-s[0],d),0,L);p=s[0]+d*t;dd=np.linalg.norm(m-p)
            if dd<best[0]: best=(dd,d)
        if best[0]>reach: continue
        n=m/np.linalg.norm(m);d=best[1]-n*np.dot(best[1],n);d/=np.linalg.norm(d)+1e-12
        ang=np.degrees(np.arccos(min(1,abs(np.dot(ev,d)))));tot+=1;ok+=(ang<tol or ang>90-tol)
    return ok/max(tot,1),tot
co,tris,_=g.icosphere(5,1.0)
th=np.linspace(-0.9,0.9,25);pts=[]
for t in th:
    p=np.array([np.cos(t),0,np.sin(t)]);c,s=np.cos(np.pi/4),np.sin(np.pi/4);pts.append(np.array([c*p[0]-s*p[1],s*p[0]+c*p[1],p[2]]))
arc=np.array([[pts[i],pts[i+1]] for i in range(len(pts)-1)])
def near_arc(p):
    return min(np.linalg.norm(p-(s[0]+(s[1]-s[0])*np.clip(np.dot(p-s[0],s[1]-s[0])/np.dot(s[1]-s[0],s[1]-s[0]),0,1))) for s in arc)
results = {}
for use in (False, True):
    for sym in (False, True):
        M.reset(); obj = M.make('sph', co, tris)
        me = obj.data; at = me.attributes.new('quadforge_guide', 'BOOLEAN', 'EDGE'); vals = []
        for e in me.edges:
            a = np.array(me.vertices[e.vertices[0]].co); b = np.array(me.vertices[e.vertices[1]].co)
            vals.append(bool(near_arc(a) < 0.03 and near_arc(b) < 0.03))
        at.data.foreach_set('value', vals)
        s = M.pipeline.RemeshSettings(); s.backend = "FORGE"; s.output = "QUADS"; s.target_quads = 3000
        s.hide_original = False; s.use_flow_guides = use; s.sym_x = sym
        r = M.pipeline.run(M.bpy.context, obj, s)
        f, n = aligned(r.object.data, arc)
        stats = r.levels[0].stats
        results[(use, sym)] = (f, stats)
        print(f"  guides {'on ' if use else 'off'} symmetry {'X' if sym else '-'}: faces {stats['faces']}, rows along the guide {100*f:.0f}% ({n} edges)")
ok = True
for sym in (False, True):
    gain = results[(True, sym)][0] - results[(False, sym)][0]
    stats = results[(True, sym)][1]
    good = gain > 0.10 and stats["border_edges"] == 0 and stats["non_manifold_edges"] == 0
    print(("PASS" if good else "FAIL"), f"flow guides symmetry {'X' if sym else 'off'}: gain {100*gain:.0f} points")
    ok &= good
print("ALL PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
