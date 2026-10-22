/* Piecewise affine visual registration. Source geometry remains immutable. */
window.Casa3DProjection = (() => {
  function create(registration) {
    const triangles = registration.triangles.map(indices => {
      const vertices = indices.map(index => registration.anchors[index]);
      const [a,b,c] = vertices.map(v => v.cad);
      const det = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
      return {vertices, a,b,c,det};
    });
    function point([x,y]) {
      for (const {vertices,a,b,c,det} of triangles) {
        const u = ((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/det;
        const v = ((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/det;
        const w = 1-u-v;
        if (u < -1e-7 || v < -1e-7 || w < -1e-7) continue;
        return [0,1].map(axis => u*vertices[0].image[axis]+v*vertices[1].image[axis]+w*vertices[2].image[axis]);
      }
      return null;
    }
    function polyline(points) {
      const projected = [];
      for (let i=0; i<points.length; i++) {
        const a = points[Math.max(0,i-1)], b=points[i];
        // Long CAD lines cross several triangles. Sample inside the segment
        // so they follow the warp rather than just joining mapped endpoints.
        const steps = i ? Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/.1)) : 1;
        for (let j=1; j<=steps; j++) {
          const mapped=point([a[0]+(b[0]-a[0])*j/steps,a[1]+(b[1]-a[1])*j/steps]);
          if (!mapped) return null;
          projected.push(mapped);
        }
      }
      return projected;
    }
    return {point,polyline};
  }
  return {create};
})();
