#!/usr/bin/env python3
"""Trace an itinerary over downloaded OSM road geometry; report every POI snap gap."""
import argparse
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path


def km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742 * math.asin(min(1, math.sqrt(h)))


def build_graph(features):
    graph = defaultdict(list)
    for f in features:
        p, g = f.get('properties', {}), f.get('geometry', {})
        if p.get('layer') != 'road' or p.get('access') in ('no', 'private') or p.get('motor_vehicle') in ('no', 'private'):
            continue
        lines = [g['coordinates']] if g.get('type') == 'LineString' else g.get('coordinates', []) if g.get('type') == 'MultiLineString' else []
        oneway = p.get('oneway', 'no')
        for line in lines:
            for a, b in zip(line, line[1:]):
                a, b = tuple(a[:2]), tuple(b[:2])
                if a == b:
                    continue
                length = km(a, b)
                info = {'osm_id': p.get('osm_id'), 'ref': p.get('ref'), 'name': p.get('name'), 'highway': p.get('highway')}
                if oneway != '-1': graph[a].append((b, length, info))
                if oneway not in ('yes', '1', 'true'): graph[b].append((a, length, info))
    return graph


def shortest(graph, start, finish):
    queue, dist, prev = [(0.0, start)], {start: 0.0}, {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != dist[node]: continue
        if node == finish: break
        for other, length, info in graph[node]:
            new = cost + length
            if new < dist.get(other, float('inf')):
                dist[other] = new
                prev[other] = (node, info)
                heapq.heappush(queue, (new, other))
    if finish not in dist: raise ValueError('No connected road path between snapped stops; acquire fuller data or check road access')
    nodes, edges = [finish], []
    while nodes[-1] != start:
        parent, info = prev[nodes[-1]]
        nodes.append(parent); edges.append(info)
    nodes.reverse(); edges.reverse()
    return nodes, edges, dist[finish]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('trip', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--trip-out', type=Path, help='Also write a routed trip JSON ready for render_map.py')
    ap.add_argument('--max-snap-km', type=float, default=15)
    args = ap.parse_args()
    trip = json.loads(args.trip.read_text(encoding='utf-8'))
    geo_path = Path(trip['map']['geojson'])
    if not geo_path.is_absolute(): geo_path = args.trip.parent / geo_path
    geo = json.loads(geo_path.read_text(encoding='utf-8'))
    graph = build_graph(geo['features'])
    if not graph: raise ValueError('No car-road graph in basemap')
    nodes = list(graph)
    pois = {p['id']: p for p in trip['pois'] if p.get('coordinate_status') != 'unresolved'}
    route_outputs = []
    for route in trip.get('routes', []):
        stops = [pois[pid] for pid in route['poi_ids']]
        snaps = []
        for p in stops:
            node = min(nodes, key=lambda n: km(n, p['coord']))
            gap = km(node, p['coord'])
            if gap > args.max_snap_km:
                raise ValueError(f'{p["name"]}: nearest mapped driving road {gap:.1f} km away; do not draw a false connector')
            snaps.append({'poi_id': p['id'], 'poi_name': p['name'], 'road_coord': list(node), 'snap_gap_km': round(gap, 3), 'poi_coord': p['coord']})
        legs = []; full = []
        for a, b in zip(snaps, snaps[1:]):
            geometry, edge_infos, distance = shortest(graph, tuple(a['road_coord']), tuple(b['road_coord']))
            refs = sorted({v['ref'] for v in edge_infos if v['ref']})
            ref_km = defaultdict(float)
            for start, end, info in zip(geometry, geometry[1:], edge_infos):
                ref_km[info.get('ref') or '未编号道路'] += km(start, end)
            leg = {'from': a['poi_id'], 'to': b['poi_id'], 'distance_km': round(distance, 1), 'road_refs': refs,
                   'ref_km': {key: round(value, 1) for key, value in sorted(ref_km.items(), key=lambda item: -item[1])},
                   'geometry': {'type': 'LineString', 'coordinates': [list(v) for v in geometry]}}
            legs.append(leg)
            full.extend(geometry if not full else geometry[1:])
        output = {'route_id': route['id'], 'method': 'shortest path on downloaded OSM major-road centerlines',
                  'limitations': 'Road access and turn restrictions may be incomplete; this is a cartographic route, not live turn-by-turn navigation.',
                  'source': str(geo_path), 'snaps': snaps, 'legs': legs,
                  'total_road_km': round(sum(v['distance_km'] for v in legs), 1),
                  'geometry': {'type': 'LineString', 'coordinates': [list(v) for v in full]}}
        route_outputs.append(output)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'routes': route_outputs}, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.trip_out:
        for route, routed in zip(trip['routes'], route_outputs):
            route['kind'] = 'routed'
            route['geometry'] = routed['geometry']
            route['source'] = 'OpenStreetMap road geometry; locally computed shortest path; ' + str(args.out)
            route['snaps'] = routed['snaps']
            route['distance_km'] = routed['total_road_km']
        args.trip_out.parent.mkdir(parents=True, exist_ok=True)
        args.trip_out.write_text(json.dumps(trip, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'route_id': r['route_id'], 'road_km': r['total_road_km'], 'stops': [(s['poi_name'], s['snap_gap_km']) for s in r['snaps']], 'legs': [(l['from'], l['to'], l['distance_km'], l['road_refs']) for l in r['legs']]} for r in route_outputs], ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
