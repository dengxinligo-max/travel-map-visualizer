#!/usr/bin/env python3
"""Download a bounded OSM vector extract and turn it into renderer-ready GeoJSON.

Python standard library only. No raster tiles, automatic endpoint rotation, or
geocoding service. Raw evidence and retrieval metadata remain beside the output.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://overpass-api.de/api/interpreter"
MAX_BYTES = 32 * 1024 * 1024
CACHE_SECONDS = 7 * 86400
ATTRIBUTION = "© OpenStreetMap contributors · ODbL"
COPYRIGHT = "https://www.openstreetmap.org/copyright"
LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"


def bbox_arg(value):
    try:
        bbox = [float(v) for v in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox needs west,south,east,north") from exc
    if (len(bbox) != 4 or not all(math.isfinite(v) for v in bbox)
            or not -180 <= bbox[0] < bbox[2] <= 180
            or not -85 <= bbox[1] < bbox[3] <= 85):
        raise argparse.ArgumentTypeError("bbox must be valid WGS84 west,south,east,north; no dateline crossing")
    return bbox


def make_query(bbox, profile):
    west, south, east, north = bbox
    box = f"({south},{west},{north},{east})"
    roads = ("way[highway]" if profile == "city" else
             'way[highway~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"]')
    selectors = [roads, 'way[waterway~"^(river|stream|canal|riverbank)$"]',
                 'way[natural="water"]', 'relation[type="multipolygon"][natural="water"]',
                 'way[landuse="reservoir"]', 'relation[type="multipolygon"][landuse="reservoir"]',
                 'way[natural~"^(wood|scrub|grassland|wetland)$"]',
                 'relation[type="multipolygon"][natural~"^(wood|scrub|grassland|wetland)$"]',
                 'way[landuse~"^(forest|grass|meadow|recreation_ground|village_green|orchard)$"]',
                 'relation[type="multipolygon"][landuse~"^(forest|grass|meadow|recreation_ground|village_green|orchard)$"]',
                 'way[leisure~"^(park|garden|nature_reserve)$"]',
                 'relation[type="multipolygon"][leisure~"^(park|garden|nature_reserve)$"]',
                 'way[boundary="administrative"]',
                 'node[place~"^(city|town|village|suburb|quarter|hamlet)$"]']
    base = "\n".join(f"  {s}{box};" for s in selectors)
    # Separate center output avoids downloading geometry of huge tourist areas.
    pois = (f'  nwr[tourism~"^(attraction|museum|viewpoint|artwork|gallery)$"][name]{box};\n'
            f'  nwr[historic][name]{box};') if profile == "city" else ""
    return ("[out:json][timeout:45][maxsize:67108864];\n(\n" + base +
            "\n);\nout body geom qt;\n" +
            ("(\n" + pois + "\n);\nout body center qt;\n" if pois else ""))


def point(v):
    if not isinstance(v, dict):
        return None
    lon, lat = v.get("lon"), v.get("lat")
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return None
    return [lon, lat]


def coordinates(raw):
    points = [point(p) for p in raw or []]
    # Never bridge missing coordinates, which would create invented geometry.
    if any(p is None for p in points):
        return []
    clean = []
    for p in points:
        if not clean or p != clean[-1]:
            clean.append(p)
    return clean


def signed_area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2


def valid_ring(ring):
    return len(ring) >= 4 and ring[0] == ring[-1] and abs(signed_area(ring)) > 1e-14


def oriented(ring, outer):
    return ring if (signed_area(ring) > 0) == outer else list(reversed(ring))


def contains(ring, p):
    x, y = p
    inside = False
    for a, b in zip(ring, ring[1:]):
        if ((a[1] > y) != (b[1] > y)) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def stitch_rings(segments):
    """Join exact OSM endpoints, including reversed ways. Do not force closure."""
    pending = [s[:] for s in segments if len(s) >= 2]
    rings = []
    while pending:
        chain = pending.pop(0)
        while chain[-1] != chain[0]:
            match = None
            for i, other in enumerate(pending):
                if chain[-1] == other[0]:
                    match = (i, other)
                    break
                if chain[-1] == other[-1]:
                    match = (i, list(reversed(other)))
                    break
            if match is None:
                return None
            i, other = match
            pending.pop(i)
            chain.extend(other[1:])
        if not valid_ring(chain):
            return None
        rings.append(chain)
    return rings


def relation_polygon(element):
    grouped = {"outer": [], "inner": []}
    for member in element.get("members", []):
        role = member.get("role", "") or "outer"
        if role not in grouped:
            continue
        if member.get("type") != "way":
            return None  # Nested multipolygons need a complete external resolver.
        line = coordinates(member.get("geometry"))
        if len(line) < 2:
            return None
        grouped[role].append(line)
    outers = stitch_rings(grouped["outer"])
    inners = stitch_rings(grouped["inner"])
    if not outers or inners is None:
        return None
    polygons = [[oriented(r, True)] for r in outers]
    for inner in inners:
        candidates = [i for i, outer in enumerate(outers) if contains(outer, inner[0])]
        if not candidates:
            return None  # An orphan hole is not safe to fill or silently discard.
        parent = min(candidates, key=lambda i: abs(signed_area(outers[i])))
        polygons[parent].append(oriented(inner, False))
    return {"type": "MultiPolygon", "coordinates": polygons}


def layer_for(tags):
    if tags.get("highway"):
        return "road"
    if tags.get("natural") == "water" or tags.get("waterway") or tags.get("landuse") == "reservoir":
        return "water"
    if (tags.get("natural") in {"wood", "scrub", "grassland", "wetland"}
            or tags.get("landuse") in {"forest", "grass", "meadow", "recreation_ground", "village_green", "orchard"}
            or tags.get("leisure") in {"park", "garden", "nature_reserve"}):
        return "green"
    if tags.get("boundary"):
        return "boundary"
    if tags.get("place"):
        return "place"
    return None


def props(element, layer):
    tags = element.get("tags", {})
    osm_id = f'{element["type"]}/{element["id"]}'
    result = {"layer": layer, "name": tags.get("name:zh") or tags.get("name", ""),
              "osm_id": osm_id, "source_url": "https://www.openstreetmap.org/" + osm_id}
    for key in ("name", "name:zh", "name:en", "highway", "ref", "oneway", "access", "motor_vehicle", "waterway", "natural", "landuse", "leisure", "place", "admin_level", "tourism", "historic"):
        if key in tags:
            result[key if key != "name" else "name_original"] = tags[key]
    return result


def convert(raw, bbox):
    if not isinstance(raw, dict) or not isinstance(raw.get("elements"), list):
        raise ValueError("Input must be an Overpass JSON object containing elements")
    if raw.get("remark"):
        raise ValueError("Overpass reported an error or incomplete result: " + str(raw["remark"])[:500])
    features = {}
    skipped = Counter()
    # Successful relation members must not also fill over the relation's holes.
    covered_ways = set()
    relation_geometries = {}
    for e in raw["elements"]:
        if e.get("type") == "relation" and layer_for(e.get("tags", {})) in {"water", "green"}:
            geom = relation_polygon(e)
            if geom:
                relation_geometries[e["id"]] = geom
                covered_ways.update((m.get("ref"), layer_for(e.get("tags", {}))) for m in e.get("members", []) if m.get("type") == "way")
    for e in raw["elements"]:
        if e.get("type") not in {"node", "way", "relation"} or "id" not in e:
            continue
        tags = e.get("tags", {})
        layer = layer_for(tags)
        geom = None
        if layer:
            if e["type"] == "node":
                p = point(e)
                if p and layer == "place":
                    geom = {"type": "Point", "coordinates": p}
            elif e["type"] == "way" and (e["id"], layer) not in covered_ways:
                line = coordinates(e.get("geometry"))
                is_area = (tags.get("area") != "no" and
                           (layer == "green" or tags.get("natural") == "water" or
                            tags.get("landuse") == "reservoir" or tags.get("waterway") == "riverbank" or
                            tags.get("area") == "yes"))
                if layer in {"green", "water"} and is_area and valid_ring(line):
                    geom = {"type": "Polygon", "coordinates": [oriented(line, True)]}
                elif layer in {"road", "boundary"} and len(line) >= 2:
                    geom = {"type": "LineString", "coordinates": line}
                elif layer == "water" and tags.get("waterway") != "riverbank" and tags.get("waterway") and len(line) >= 2:
                    geom = {"type": "LineString", "coordinates": line}
            elif e["type"] == "relation":
                geom = relation_geometries.get(e["id"])
            if geom:
                p = props(e, layer)
                fid = f'{p["osm_id"]}:{layer}'
                features[fid] = {"type": "Feature", "id": fid, "properties": p, "geometry": geom}
            elif (e.get("id"), layer) not in covered_ways:
                skipped["missing_or_unclosed_" + layer] += 1
        is_poi = tags.get("name") and (tags.get("tourism") in {"attraction", "museum", "viewpoint", "artwork", "gallery"} or tags.get("historic"))
        if is_poi:
            p = point(e) if e["type"] == "node" else point(e.get("center", {}))
            precision = "osm_node" if e["type"] == "node" else "osm_bbox_center"
            if p:
                west, south, east, north = bbox
                if west <= p[0] <= east and south <= p[1] <= north:
                    properties = props(e, "poi")
                    properties["coordinate_precision"] = precision
                    properties["verification"] = "osm_candidate_not_independently_verified"
                    fid = f'{properties["osm_id"]}:poi'
                    features[fid] = {"type": "Feature", "id": fid, "properties": properties, "geometry": {"type": "Point", "coordinates": p}}
    result = list(features.values())
    geographic = [f for f in result if f["geometry"]["type"] != "Point"]
    if not geographic:
        raise ValueError("No usable roads, water, green areas or boundaries: refusing an empty basemap. Inspect raw JSON, reduce bbox, or choose another licensed extract.")
    return result, dict(skipped)


def read_raw(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Raw input exceeds 32 MiB safety limit; use a smaller area or offline GIS tooling")
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(endpoint, query, user_agent):
    if urllib.parse.urlparse(endpoint).scheme != "https":
        raise ValueError("Endpoint must use HTTPS")
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={"User-Agent": user_agent,
                                    "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Response exceeds 32 MiB; reduce bbox, use region profile, or download a regional extract")
            return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in {429, 502, 503, 504}:
                raise
            retry_after = exc.headers.get("Retry-After", "5")
            delay = int(retry_after) if retry_after.isdigit() else 5
            if delay > 30:
                raise RuntimeError(f"Server requested {delay}s delay; stop and try later") from exc
            print(f"Overpass HTTP {exc.code}; one retry after {max(5, delay)}s", file=sys.stderr)
            time.sleep(max(5, delay))
        except (urllib.error.URLError, TimeoutError):
            if attempt:
                raise
            print("Temporary connection error; retrying once after 5s", file=sys.stderr)
            time.sleep(5)
    raise RuntimeError("No response")


def write_json(path, obj):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, type=bbox_arg, help="WGS84 west,south,east,north; prefix negative value with =")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--profile", choices=["city", "region"], default="city")
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--user-agent", default="TravelMapVisualizer/1.0 (bounded local map export)")
    parser.add_argument("--input", type=Path, help="Convert already downloaded Overpass JSON without network")
    parser.add_argument("--use-cache", action="store_true", help="Opt in to reuse a matching <7-day extract; default downloads fresh")
    args = parser.parse_args()
    west, south, east, north = args.bbox
    area = (east - west) * 111.32 * math.cos(math.radians((south + north) / 2)) * (north - south) * 111.32
    limit = 2500 if args.profile == "city" else 50000
    if not args.input and (area > limit or east - west > 12 or north - south > 12):
        parser.error(f"{args.profile} download is limited to {limit:,} km² and 12° spans (requested ≈{area:,.0f} km²). Use a regional data extract or a smaller map extent.")
    if len(args.user_agent.strip()) < 12:
        parser.error("Supply an identifying application User-Agent, preferably with a contact URL")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    raw_path = args.out.with_suffix(".raw.json")
    meta_path = args.out.with_suffix(".metadata.json")
    query = make_query(args.bbox, args.profile)
    signature = hashlib.sha256((args.endpoint + "\n" + query).encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    old_meta = {}
    if meta_path.exists():
        try:
            old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    cached = (args.use_cache and not args.input and raw_path.exists()
              and old_meta.get("status") == "ok"
              and old_meta.get("query_sha256") == signature
              and time.time() - raw_path.stat().st_mtime < CACHE_SECONDS)
    try:
        if args.input:
            raw = read_raw(args.input)
            retrieval = "offline_input"
        elif cached:
            raw = read_raw(raw_path)
            retrieval = "cache"
        else:
            raw = fetch(args.endpoint, query, args.user_agent)
            retrieval = "network"
    except (ValueError, OSError, RuntimeError) as exc:
        write_json(meta_path, {"status": "failed", "stage": "download_or_read", "error": str(exc),
                              "attempted_at_utc": now, "bbox": args.bbox,
                              "endpoint": args.endpoint if not args.input else None,
                              "existing_output_is_stale": args.out.exists()})
        raise
    # Preserve diagnostic evidence even if conversion rejects incomplete content.
    if retrieval != "cache" and (not args.input or args.input.resolve() != raw_path.resolve()):
        write_json(raw_path, raw)
    try:
        features, skipped = convert(raw, args.bbox)
    except ValueError as exc:
        write_json(meta_path, {"status": "failed", "error": str(exc), "attempted_at_utc": now,
                              "endpoint": args.endpoint if not args.input else None, "bbox": args.bbox, "query": query})
        raise
    metadata = {"status": "ok", "attribution": ATTRIBUTION, "attribution_url": COPYRIGHT,
                "license": "ODbL-1.0", "license_url": LICENSE, "coordinate_system": "WGS84 / EPSG:4326",
                "bbox": args.bbox, "profile": args.profile, "retrieval": retrieval,
                "retrieved_at_utc": old_meta.get("retrieved_at_utc") if cached else (now if retrieval == "network" else None),
                "converted_at_utc": now, "osm_data_timestamp": raw.get("osm3s", {}).get("timestamp_osm_base"),
                "endpoint": args.endpoint if not args.input else None,
                "input_file": str(args.input.resolve()) if args.input else None,
                "query_sha256": signature, "query": query, "raw_file": raw_path.name,
                "feature_count": len(features), "layers": dict(Counter(f["properties"]["layer"] for f in features)),
                "skipped": skipped,
                "limitations": ["OSM coverage varies; extracted POIs are candidates requiring name and location checks.",
                                "Area POI centers are bounding-box centers, not verified entrance locations.",
                                "Incomplete or unsupported polygon relations are omitted, never force-closed.",
                                "Public boundary data is a cartographic reference, not an official border determination."]}
    def all_coords(value):
        if value and isinstance(value[0], (int, float)):
            yield value
        else:
            for part in value:
                yield from all_coords(part)
    points = [c for feature in features for c in all_coords(feature["geometry"]["coordinates"])]
    actual_bbox = [min(c[0] for c in points), min(c[1] for c in points),
                   max(c[0] for c in points), max(c[1] for c in points)]
    write_json(args.out, {"type": "FeatureCollection", "bbox": actual_bbox, "metadata": metadata, "features": features})
    write_json(meta_path, metadata)
    print(json.dumps({"out": str(args.out.resolve()), "features": len(features), "layers": metadata["layers"], "retrieval": retrieval,
                      "raw": str(raw_path.resolve()), "metadata": str(meta_path.resolve()), "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print("Map extraction failed: " + str(error), file=sys.stderr)
        sys.exit(1)
