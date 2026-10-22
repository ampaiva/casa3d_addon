"""Import Veredas electrical reference (DXF converted locally from the original DWG).

Usage: python scripts/import_electrical_dxf.py SOURCE.dxf [--output assets]
Requires ezdxf only for this offline import; the application has no CAD dependency.
Coordinates stay in the CAD reference frame; no 3D alignment is inferred.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

FLOORS = {'terreo': (5, 16, 41, 85), 'superior': (139, 16, 170, 85)}


def import_reference(source, output):
    import ezdxf
    from ezdxf import disassemble, path

    document = ezdxf.readfile(source)
    entities = list(disassemble.recursive_decompose(document.modelspace()))
    output.mkdir(parents=True, exist_ok=True)
    result = {
        'source': 'DWG fornecido pelo usuário, convertido localmente para DXF',
        'classification': {'DOT2': 'piso', 'Continuous': 'teto_laje',
                           'status': 'Convenção aceita pelo usuário; execução a conferir na obra.'},
        'real_boards': {'room': 'Depósito', 'connections': 'A conferir na obra',
                        'source': 'Usar infrastructure.panels; não deduzir posição real ou ligação de QD1/QM1 do projeto.'},
        'floors': {},
    }
    for floor, bounds in FLOORS.items():
        xmin, ymin, xmax, ymax = bounds
        prefix = 'T' if floor == 'terreo' else 'S'
        routes, drawing, diagonals = [], [], defaultdict(set)
        for entity in entities:
            layer = entity.dxf.layer
            if layer.startswith(('DI-', 'TE-', 'TV-')):
                continue
            if entity.dxftype() not in ('LINE', 'ARC', 'CIRCLE', 'LWPOLYLINE', 'POLYLINE', 'SPLINE', 'ELLIPSE'):
                continue
            try:
                points = [(v.x, v.y) for v in path.make_path(entity).flattening(.025)]
            except (TypeError, ValueError, NotImplementedError):
                continue
            if len(points) < 2 or not all(xmin <= x <= xmax and ymin <= y <= ymax for x, y in points):
                continue
            # Square-with-X symbol in P-SIMBOL, matching the legend; do not
            # mistake arbitrary conduit crossings for boxes.
            if layer == 'P-SIMBOL' and entity.dxftype() == 'LINE':
                a, b = points[0], points[-1]
                dx, dy = b[0]-a[0], b[1]-a[1]
                if abs(abs(dx)-.53) < .002 and abs(abs(dy)-.53) < .002:
                    center = (round((a[0]+b[0])/2, 5), round((a[1]+b[1])/2, 5))
                    diagonals[center].add(1 if dx*dy > 0 else -1)
            if layer == 'EL-Condutos' and entity.dxftype() in ('LINE', 'LWPOLYLINE', 'SPLINE', 'ARC'):
                ltype = entity.dxf.get('linetype', 'BYLAYER')
                if ltype.upper() == 'BYLAYER':
                    ltype = document.layers.get(layer).dxf.linetype
                if ltype.upper() not in ('DOT2', 'CONTINUOUS'):
                    raise ValueError(f'Unclassified conduit line type: {ltype}')
                routes.append({'id': f'ED-{prefix}{len(routes)+1:03}',
                               'location': 'piso' if ltype.upper() == 'DOT2' else 'teto_laje',
                               'source_handle': entity.dxf.handle,
                               'points': [[round(x, 5), round(y, 5)] for x, y in points], 'boxes': []})
            elif not layer.startswith('EL-'):
                drawing.append('M'+' L'.join(f'{(x-xmin)*10:.2f},{(ymax-y)*10:.2f}' for x, y in points))
        boxes = []
        for x, y in sorted((center for center, slopes in diagonals.items() if slopes == {-1, 1}), key=lambda p: -p[1]):
            box = {'id': f'CP-{prefix}{len(boxes)+1:02}', 'x': x, 'y': y,
                   'size_mm': [150, 150, 80], 'status': 'Conforme projeto; verificar na obra', 'connected_routes': []}
            for route in routes:
                if min(((p[0]-x)**2+(p[1]-y)**2)**.5 for p in (route['points'][0], route['points'][-1])) <= .42:
                    route['boxes'].append(box['id'])
                    box['connected_routes'].append(route['id'])
            boxes.append(box)
        result['floors'][floor] = {'bounds': bounds, 'routes': routes, 'boxes': boxes}
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {(xmax-xmin)*10} {(ymax-ymin)*10}">'
               '<g fill="none" stroke="#727b83" stroke-width=".35">'
               + ''.join(f'<path d="{d}"/>' for d in drawing) + '</g></svg>')
        (output / f'conduits-{floor}.svg').write_text(svg)
    (output / 'conduits.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'assets')
    args = parser.parse_args()
    result = import_reference(args.source, args.output)
    for name, info in result['floors'].items():
        print(f"{name}: {len(info['routes'])} trechos; {len(info['boxes'])} caixas")
