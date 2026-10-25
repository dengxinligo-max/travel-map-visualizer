#!/usr/bin/env python3
"""Render WGS84 trip JSON + GeoJSON to a self-contained SVG/HTML travel poster."""
import argparse
import html
import json
import math
import re
from pathlib import Path

ESC = lambda v: html.escape(str(v), quote=True)
FONT = '"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif'


def txt(x, y, value, size=20, fill='#171b15', weight=500, extra=''):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" font-weight="{weight}" {extra}>{ESC(value)}</text>'


def rect(x, y, w, h, fill, radius=0, extra=''):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" {extra}/>'


def text_width(s, size):
    return sum(1 if ord(c) > 255 else .57 for c in str(s)) * size


def wrap(value, width, size=18):
    lines, line, w = [], '', 0
    for c in str(value):
        d = size * (1 if ord(c) > 255 else .57)
        if c == '\n' or (w + d > width and line):
            lines.append(line)
            line, w = '', 0
        if c != '\n':
            line += c
            w += d
    if line:
        lines.append(line)
    return lines


def coord_ok(c):
    return isinstance(c, list) and len(c) >= 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c[:2]) and -180 <= c[0] <= 180 and -85 <= c[1] <= 85


def merc(c):
    return math.radians(c[0]), math.log(math.tan(math.pi / 4 + math.radians(c[1]) / 2))


def color(c):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', c):
        raise ValueError('Route colors must be #RRGGBB')
    return c


def validate(d):
    if d.get('version') != 1:
        raise ValueError('version must be 1')
    if not d.get('title'):
        raise ValueError('title is required')
    m = d['map']
    if m.get('crs') != 'EPSG:4326':
        raise ValueError('Map and all coordinates must be WGS84 EPSG:4326')
    b = m['bbox']
    if len(b) != 4 or not coord_ok(b[:2]) or not coord_ok(b[2:]) or b[0] >= b[2] or b[1] >= b[3]:
        raise ValueError('bbox must be west,south,east,north; split antimeridian maps')
    if not m.get('attribution') or not m.get('source_url') or not m.get('retrieved_at'):
        raise ValueError('Map attribution, source_url and retrieved_at are required')
    if d.get('mode', 'city') not in ['city', 'region', 'multi-route']:
        raise ValueError('mode must be city, region or multi-route')
    ids = set()
    for p in d['pois']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', p['id']) or p['id'] in ids:
            raise ValueError('POI IDs must be unique ASCII slugs')
        ids.add(p['id'])
        if p.get('coordinate_status') not in ['verified', 'approximate', 'unresolved']:
            raise ValueError(f'{p["id"]}: missing coordinate_status')
        if p['coordinate_status'] != 'unresolved':
            if not coord_ok(p.get('coord')) or not p.get('coordinate_source'):
                raise ValueError(f'{p["id"]}: valid coordinates and coordinate_source required')
            x, y = p['coord'][:2]
            if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
                raise ValueError(f'{p["id"]}: outside map bbox; expand bbox or create inset')
    route_ids = set()
    for r in d.get('routes', []):
        if r['id'] in route_ids:
            raise ValueError('Duplicate route ID')
        route_ids.add(r['id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]+', r['id']):
            raise ValueError('Route IDs must be ASCII slugs')
        if r.get('kind') not in ['schematic', 'routed', 'user_track']:
            raise ValueError('Route kind must be schematic, routed or user_track')
        if any(p not in ids for p in r['poi_ids']):
            raise ValueError('Route references unknown POI')
        color(r.get('color', '#327bef'))
        if r['kind'] != 'schematic':
            g = r.get('geometry', {})
            if g.get('type') != 'LineString' or len(g.get('coordinates', [])) < 2 or not all(coord_ok(c) for c in g['coordinates']) or not r.get('source'):
                raise ValueError('Actual route requires valid LineString geometry and source')
            if any(not (b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3]) for c in g['coordinates']):
                raise ValueError('Actual route leaves bbox; expand map range')
    for label in d.get('map_labels', []):
        if not coord_ok(label.get('coord')) or not label.get('name'):
            raise ValueError('Map landmark labels require a name and WGS84 coordinate')
        if label.get('kind') not in ('road', 'river', 'county', 'town', 'landmark'):
            raise ValueError('Map landmark kind must be road, river, county, town or landmark')


def render(d, geo, out):
    validate(d)
    if geo.get('type') != 'FeatureCollection' or not geo.get('features'):
        raise ValueError('Real map GeoJSON features are required; refusing blank basemap')
    w, h = 1080, int(d.get('height', 1440))
    if h < 1440 or h > 2400:
        raise ValueError('Poster height must be 1440..2400; split larger maps')
    b = d['map']['bbox']
    # One uniform Web Mercator transform for map, POIs and route geometries.
    xmin, ymin = merc(b[:2]); xmax, ymax = merc(b[2:])
    map_top, map_bottom = 340, h - 320
    inner = (42, map_top + 26, 996, map_bottom - map_top - 46)
    scale = min(inner[2] / (xmax-xmin), inner[3] / (ymax-ymin))
    cx, cy = (xmin+xmax)/2, (ymin+ymax)/2
    def project(c):
        x, y = merc(c)
        return 540 + (x-cx)*scale, (map_top+map_bottom)/2 - (y-cy)*scale
    def line(coords, close=False):
        if not all(coord_ok(c) for c in coords):
            return ''
        pts = [project(c) for c in coords]
        return ' '.join(('M' if i == 0 else 'L') + f'{x:.1f},{y:.1f}' for i,(x,y) in enumerate(pts)) + ('Z' if close and pts else '')
    def geometry(g):
        t, c = g.get('type'), g.get('coordinates', [])
        if t == 'LineString': return line(c)
        if t == 'MultiLineString': return ' '.join(line(x) for x in c)
        if t == 'Polygon': return ' '.join(line(x, True) for x in c)
        if t == 'MultiPolygon': return ' '.join(line(r, True) for p in c for r in p)
        return ''
    warnings, unresolved = [], [p for p in d['pois'] if p['coordinate_status'] == 'unresolved']
    placed = [p for p in d['pois'] if p['coordinate_status'] != 'unresolved']
    if not placed:
        raise ValueError('No located POIs to render')
    coords = {p['id']:project(p['coord']) for p in placed}
    by_id = {p['id']:p for p in d['pois']}
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="maptitle mapdesc" style="font-family:{ESC(FONT)}">',
        f'<title id="maptitle">{ESC(d["title"])}</title><desc id="mapdesc">{ESC(d.get("summary", "旅行路线与景点位置"))}</desc>',
        f'<defs><clipPath id="mapclip"><rect x="0" y="258" width="1080" height="{h-560}"/></clipPath><linearGradient id="paper"><stop stop-color="#f7f5eb"/><stop offset="1" stop-color="#eef2e4"/></linearGradient></defs>',
        rect(0,0,w,h,'#f7f5ec'),rect(0,258,w,h-560,'url(#paper)')]
    # Draw polygons first. Polygon holes retain fill-rule=evenodd.
    layers = {'green': [], 'water': [], 'boundary': [], 'road': []}
    counts = {k:0 for k in layers}
    for f in geo['features']:
        prop, g = f.get('properties') or {}, f.get('geometry') or {}
        layer = prop.get('layer')
        if layer not in layers: continue
        def nested_coords(value):
            if not value:
                return
            if isinstance(value[0], (int, float)):
                if coord_ok(value): yield value
            else:
                for part in value: yield from nested_coords(part)
        vertices = list(nested_coords(g.get('coordinates', [])))
        if not vertices:
            continue
        # Reject wrong-area data rather than counting off-screen paths as a basemap.
        gx0, gx1 = min(c[0] for c in vertices), max(c[0] for c in vertices)
        gy0, gy1 = min(c[1] for c in vertices), max(c[1] for c in vertices)
        if gx1 < b[0] or gx0 > b[2] or gy1 < b[1] or gy0 > b[3]:
            continue
        path = geometry(g)
        if not path: continue
        counts[layer] += 1
        polygon = g.get('type') in ['Polygon','MultiPolygon']
        if layer == 'green': style = 'fill="#d8e7c9" stroke="#c7d9b8" stroke-width=".8"' if polygon else 'fill="none" stroke="#c7d9b8" stroke-width="1"'
        elif layer == 'water': style = 'fill="#b9d9df" stroke="#a5cbd3" stroke-width="1.2"' if polygon else 'fill="none" stroke="#afd1da" stroke-width="2.5"'
        elif layer == 'boundary': style = 'fill="none" stroke="#91ab7c" stroke-width="1" stroke-dasharray="4 5"'
        else:
            major = prop.get('highway') in ['motorway','trunk','primary','secondary','tertiary']
            style = f'fill="none" stroke="{"#e5d3a0" if major else "#ffffff"}" stroke-width="{3.5 if major else 1.2}" stroke-linecap="round"'
        layers[layer].append(f'<path d="{path}" {style} fill-rule="evenodd"/>')
    if not sum(counts.values()):
        raise ValueError('No supported basemap geometry intersects the requested area; check bbox and map data')
    s.append('<g clip-path="url(#mapclip)" aria-label="真实地图底图">')
    for k in ['green','water','boundary','road']: s.extend(layers[k])
    s.append('</g>')
    # Editorial heading; fits content instead of hardcoded title length.
    s += [rect(32,24,1016,38,'#151a12',19),txt(52,50,d.get('region','旅行地图'),18,'#ffffff',650),
          txt(1024,50,d.get('eyebrow','TRAVEL FIELD NOTES'),15,'#e7efdb',600,'text-anchor="end"')]
    title = d['title']; title_size = min(110, 990 / max(1, text_width(title, 1)))
    s += [txt(36,174,title,title_size,'#10170e',900,'letter-spacing="-3"'),
          txt(42,220,d.get('subtitle','把旅程放回地图里'),26,'#496536',650),
          txt(1038,220,d.get('edition','文字攻略 → 地图可视化'),16,'#64715b',500,'text-anchor="end"'),
          rect(36,249,1008,3,'#182213')]
    # Legend strip.
    s.append(rect(36,276,1008,55,'#ffffff',12,'fill-opacity=".72"'))
    legend = [(65,'#effb32','#171b15','必去'),(214,'#171b15','#ffffff','推荐'),(363,'#66844f','#ffffff','顺路')]
    for x,fill,stroke,label in legend:
        s += [f'<circle cx="{x}" cy="303" r="9" fill="{fill}" stroke="{stroke}" stroke-width="3"/>', txt(x+19,310,label,18,weight=650)]
    schematic = any(r['kind']=='schematic' for r in d.get('routes', []))
    route_note = '虚线仅示游览顺序 · 非导航路径' if schematic else '沿实际道路绘制 · 非实时导航'
    s += [txt(1018,310,route_note,16,'#5b6754',500,'text-anchor="end"')]
    # Subtle area labels are explicit user data, never inferred boundaries.
    for a in d.get('area_labels', []):
        if coord_ok(a.get('coord')):
            x,y=project(a['coord']);s.append(txt(x,y,a['name'],a.get('size',32),'#638079',650,'text-anchor="middle" opacity=".65"'))
    # Geographic labels use source-linked coordinates; no invented county outlines.
    label_colors = {'road':'#6e622f','river':'#417f9a','county':'#4b613c','town':'#526942','landmark':'#456a6b'}
    geo_label_svg = []
    for label in d.get('map_labels', []):
        x,y = project(label['coord'])
        if not (-30 <= x <= w+30 and map_top-20 <= y <= map_bottom+20): continue
        kind = label['kind']; size = 22 if kind=='county' else 17 if kind in ('road','river') else 16
        name = label['name']
        geo_label_svg.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="{size}" font-weight="{750 if kind=="county" else 650}" fill="{label_colors[kind]}" stroke="#f7f5ec" stroke-width="5" stroke-linejoin="round" paint-order="stroke" opacity=".93">{ESC(name)}</text>')
    # Routes under leaders and markers. Unresolved intermediate stops break paths.
    for r in d.get('routes', []):
        rc=color(r.get('color','#327bef')); rid=ESC(r['id'])
        s.append(f'<g class="route" data-route="{rid}">')
        if r['kind']=='schematic':
            seg=[]; segments=[]
            for pid in r['poi_ids']:
                if pid in coords: seg.append(by_id[pid]['coord'])
                else:
                    if len(seg)>1: segments.append(seg)
                    seg=[]
            if len(seg)>1: segments.append(seg)
            for seg in segments:
                pth=line(seg)
                s += [f'<path d="{pth}" fill="none" stroke="{rc}" stroke-width="27" opacity=".13" stroke-linejoin="round"/>',
                      f'<path d="{pth}" fill="none" stroke="#fffdf4" stroke-width="9" stroke-linejoin="round"/>',
                      f'<path d="{pth}" fill="none" stroke="{rc}" stroke-width="4.5" stroke-dasharray="7 8" stroke-linecap="round" stroke-linejoin="round"/>']
        else:
            pth=geometry(r['geometry'])
            s += [f'<path d="{pth}" fill="none" stroke="#fffef6" stroke-width="12" stroke-linejoin="round" stroke-linecap="round"/>',f'<path d="{pth}" fill="none" stroke="#1b2718" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"/>',f'<path d="{pth}" fill="none" stroke="{rc}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>']
            for snap in r.get('snaps', []):
                if snap.get('snap_gap_km', 0) >= 1 and snap.get('poi_id') in by_id:
                    p = by_id[snap['poi_id']]
                    connector = line([snap['road_coord'], p['coord']])
                    s.append(f'<path d="{connector}" fill="none" stroke="#a36e39" stroke-width="2" stroke-dasharray="4 5"/>')
        s.append('</g>')
    s.extend(geo_label_svg)
    # Labels sorted north-to-south on each side, with measured wrapped boxes.
    sides={'left':[],'right':[]}
    for p in placed:
        side=p.get('label_side') or ('left' if coords[p['id']][0]<540 else 'right')
        if side not in sides: raise ValueError('label_side must be left or right')
        sides[side].append(p)
    label_boxes=[]
    for side, points in sides.items():
        points.sort(key=lambda p:coords[p['id']][1])
        box_width=242
        heights=[]
        for p in points:
            lines=wrap(p.get('note',''),box_width-20,17)
            if len(lines)>3:
                warnings.append(f'{p["id"]}: note shortened on map; full text in HTML details')
                lines=lines[:3]; lines[-1]=lines[-1][:-1]+'…'
            p['_lines']=lines
            heights.append(82+23*len(lines))
        capacity=map_bottom-map_top-24
        if sum(heights)+16*max(0,len(points)-1)>capacity:
            raise ValueError(f'Too many {side} labels for this page: increase height or split map')
        # Spread with natural position preference then enforce a non-overlap stack.
        ys=[]; previous=map_top
        for p,bh in zip(points,heights):
            desired=coords[p['id']][1]-35
            yy=max(previous,desired)
            ys.append(yy);previous=yy+bh+16
        if ys:
            overflow=max(0,ys[-1]+heights[-1]-(map_bottom-10))
            ys=[y-overflow for y in ys]
            for i in range(len(ys)):
                ys[i]=max(map_top,ys[i],ys[i-1]+heights[i-1]+16 if i else map_top)
        x=38 if side=='left' else 800
        for p,y,bh in zip(points,ys,heights):
            px,py=coords[p['id']];anchor_x=x+box_width if side=='left' else x
            joint_x=anchor_x+16 if side=='left' else anchor_x-16
            leader=f'M{px:.1f},{py:.1f} L{joint_x},{py:.1f} L{joint_x},{y+22:.1f} L{anchor_x},{y+22:.1f}'
            s.append(f'<path d="{leader}" fill="none" stroke="#27361f" stroke-width="1.25" stroke-dasharray="3 4" opacity=".75"/>')
            s.append(f'<g class="poi-label" data-poi="{ESC(p["id"])}" role="button" tabindex="0" aria-label="查看{ESC(p["name"])}详情">')
            s.append(rect(x-8,round(y-8,1),box_width+16,bh,'#fffef7',8,'fill-opacity=".88"'))
            name_size=min(24,(box_width-18)/max(1,text_width(p['name'],1)))
            if name_size<18: warnings.append(f'{p["id"]}: long title; use short display name')
            name_width=min(box_width,text_width(p['name'],name_size)+20)
            fill='#effb32' if p.get('priority')=='must' else '#1b2416'
            ink='#17220f' if p.get('priority')=='must' else '#ffffff'
            s += [rect(x,round(y,1),round(name_width,1),34,fill,17),txt(x+10,y+25,p['name'],name_size,ink,750)]
            meta=' · '.join(str(z) for z in [p.get('day'),p.get('fee'), '约位' if p['coordinate_status']=='approximate' else None] if z)
            s.append(txt(x+3,y+56,meta,15,'#66765a',650))
            for j,l in enumerate(p['_lines']): s.append(txt(x+3,y+82+j*23,l,17,'#384432'))
            s.append('</g>')
            label_boxes.append({'id':p['id'],'x':x-8,'y':round(y-8,1),'w':box_width+16,'h':bh})
    # POI point and order numeral always attached to true coordinate.
    for i,p in enumerate(placed):
        x,y=coords[p['id']]; priority=p.get('priority','recommended')
        fill='#effb32' if priority=='must' else '#638247' if priority=='optional' else '#1b2416'
        s.append(f'<g class="poi-marker" data-poi="{ESC(p["id"])}" role="button" tabindex="0" aria-label="{ESC(p["name"])}">')
        s += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="15" fill="{fill}" stroke="#ffffff" stroke-width="4"/>',txt(x,y+5,str(i+1),13,'#17220f' if priority=='must' else '#ffffff',750,'text-anchor="middle"'),'</g>']
    # Compass and scale, computed from the actual projection.
    s += [txt(992,380,'北',18,'#496536',750,'text-anchor="middle"'),'<path d="M992 392 L977 432 L992 424 L1007 432 Z" fill="#486335" stroke="#ffffff" stroke-width="2"/>']
    meters_per_px=6378137*math.cos(math.radians((b[1]+b[3])/2))/scale
    ideal=100*meters_per_px;mag=10**math.floor(math.log10(ideal));dist=max(n*mag for n in [1,2,5,10] if n*mag<=ideal)
    bar=dist/meters_per_px
    s += [f'<path d="M45 {map_bottom-8} v7 h{bar:.1f} v-7" fill="none" stroke="#526348" stroke-width="2"/>',txt(45,map_bottom-18,f'{dist/1000:g} km' if dist>=1000 else f'{dist:g} m',14,'#526348')]
    # Bottom trip cards.
    routes=d.get('routes',[])
    if len(routes)>4: raise ValueError('At most 4 route cards per page; split map')
    card_y=h-280; n=max(1,len(routes)); cw=(1008-18*(n-1))/n
    for i,r in enumerate(routes):
        x=36+i*(cw+18);rc=color(r.get('color','#327bef'))
        s += [rect(round(x,1),card_y,round(cw,1),145,'#ffffff',9,'fill-opacity=".85"'),rect(round(x,1),card_y,round(cw,1),5,rc),txt(x+16,card_y+39,r['id'],28,rc,850)]
        name_lines=wrap(r['name'],cw-102,19)
        name=name_lines[0] if name_lines else ''
        if len(name_lines)>1:
            name=name[:-1]+'…'
            warnings.append(f'{r["id"]}: route title shortened; full name in HTML controls and data')
        s.append(txt(x+85,card_y+37,name,19,'#25331d',750))
        description=wrap(r.get('description',''),cw-32,17)
        if len(description)>3:
            description=description[:3];description[-1]=description[-1][:-1]+'…'
            warnings.append(f'{r["id"]}: route description shortened; split or condense the text')
        for j,l in enumerate(description): s.append(txt(x+16,card_y+69+j*24,l,17,'#4d5b44'))
    tips=d.get('tips',[])[:2]
    for i,t in enumerate(tips):
        if text_width(t,15)>980: warnings.append('Footer tip shortened; full tip preserved in trip.json')
        s.append(txt(42,h-104+i*23,wrap('提示 / '+t,990,15)[0],15,'#516049'))
    s.append(rect(36,h-48,1008,1,'#b7c1ab'))
    footer=f'{d["map"]["attribution"]}  ·  数据获取 {d["map"]["retrieved_at"]}'
    fs=min(13,990/max(1,text_width(footer,1)))
    url=d['map']['source_url']
    if not re.match(r'^https?://',url): raise ValueError('source_url must be http(s)')
    s += [f'<a href="{ESC(url)}">',txt(42,h-24,footer,fs,'#68745e'),'</a>','</svg>']
    svg='\n'.join(s)
    for p in placed: p.pop('_lines',None)
    # Detect text-box collisions with any unrelated map marker, do not silently certify.
    for box in label_boxes:
        for pid,(px,py) in coords.items():
            if box['x']-16<px<box['x']+box['w']+16 and box['y']-16<py<box['y']+box['h']+16:
                warnings.append(f'Label {box["id"]} overlaps marker {pid}; adjust bbox/side/height or split map')
    for i,p in enumerate(placed):
        for q in placed[i+1:]:
            if math.dist(coords[p['id']],coords[q['id']])<34:
                warnings.append(f'Markers {p["id"]} and {q["id"]} overlap; use a separate detail map')
    report={'status':'rendered-needs-visual-review','dimensions':[w,h],'map_features':counts,'poi_count':len(placed),'unresolved':[p['name'] for p in unresolved],'warnings':warnings,'projection':'Web Mercator, uniform scale; WGS84 input','label_boxes':label_boxes}
    out.mkdir(parents=True,exist_ok=True)
    (out/'poster.svg').write_text(svg,encoding='utf-8')
    payload=json.dumps(d,ensure_ascii=False).replace('<','\\u003c').replace('&','\\u0026')
    controls=''.join(f'<button class="route-toggle" data-route="{ESC(r["id"])}" aria-pressed="true">{ESC(r["id"])} · {ESC(r["name"])}</button>' for r in routes)
    content=HTML.replace('__TITLE__',ESC(d['title'])).replace('__CONTROLS__',controls).replace('__SVG__',svg).replace('__DATA__',payload)
    (out/'index.html').write_text(content,encoding='utf-8')
    (out/'render-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


HTML='''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title><style>
*{box-sizing:border-box}body{margin:0;background:#e5e8df;color:#23311c;font-family:"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif}.toolbar{max-width:1080px;margin:20px auto;display:flex;flex-wrap:wrap;gap:8px;padding:0 12px;align-items:center}.toolbar p{font-size:13px;margin:4px 8px}.toolbar button{border:1px solid #b2bdaa;background:#fffef7;padding:10px 15px;border-radius:30px;color:#26391a;font:inherit;cursor:pointer}.toolbar button[aria-pressed=false]{opacity:.45}#poster{width:1080px;max-width:100%;margin:auto;box-shadow:0 20px 80px #26391a18;background:#f7f5ec}#poster>svg{width:100%;height:auto;display:block}.poi-marker,.poi-label{cursor:pointer}.poi-marker:focus,.poi-label:focus{outline:none;filter:drop-shadow(0 0 6px #387eea)}.muted{opacity:.08;pointer-events:none}#detail{max-width:1080px;margin:18px auto 32px;padding:22px;background:#fffef7;border-radius:12px;font-size:16px;line-height:1.7}#detail h2{margin:0;font-size:22px}#detail p{margin:8px 0}.source-link{word-break:break-all}button:focus-visible,a:focus-visible{outline:3px solid #347fee;outline-offset:3px}@media print{.toolbar,#detail{display:none}#poster{box-shadow:none;margin:0}@page{margin:0}}
</style></head><body><nav class="toolbar" aria-label="地图工具">__CONTROLS__<button id="reset">显示全部</button><button id="png">下载高清 PNG</button><button id="svg">下载 SVG</button><p id="status" role="status">点击景点查看详情 · 地图可离线使用</p></nav><article id="poster">__SVG__</article><section id="detail" aria-live="polite"><h2>文字攻略，放回地图里</h2><p>点击图中景点，查看备注与坐标来源。上方按钮可筛选路线。</p></section><script type="application/json" id="trip-data">__DATA__</script><script>
const trip=JSON.parse(document.getElementById('trip-data').textContent),poster=document.querySelector('#poster svg'),detail=document.getElementById('detail');
function para(value){const p=document.createElement('p');p.textContent=value;detail.append(p)}
function link(value){if(!/^https?:\\/\\//.test(value)){para(value);return}const a=document.createElement('a');a.href=value;a.textContent=value;a.className='source-link';a.rel='noreferrer';a.target='_blank';detail.append(a)}
function showPoi(id){const p=trip.pois.find(x=>x.id===id);detail.replaceChildren();const h=document.createElement('h2');h.textContent=p.name;detail.append(h);para([p.day,p.fee,p.coordinate_status==='approximate'?'位置为近似值':''].filter(Boolean).join(' · '));para(p.note||'');if(p.original_text)para('原文：'+p.original_text);para('坐标：'+p.coord.join(', '));link(p.coordinate_source);}
document.querySelectorAll('[data-poi]').forEach(el=>{el.addEventListener('click',()=>showPoi(el.dataset.poi));el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();showPoi(el.dataset.poi)}})});
document.querySelectorAll('.route-toggle').forEach(b=>b.addEventListener('click',()=>{const active=b.getAttribute('aria-pressed')!=='true';b.setAttribute('aria-pressed',String(active));document.querySelectorAll('.route').forEach(g=>{if(g.dataset.route===b.dataset.route)g.classList.toggle('muted',!active)})}));
document.getElementById('reset').addEventListener('click',()=>{document.querySelectorAll('.route').forEach(g=>g.classList.remove('muted'));document.querySelectorAll('.route-toggle').forEach(b=>b.setAttribute('aria-pressed','true'))});
function save(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000)}
function svgBlob(){const clone=poster.cloneNode(true);clone.querySelectorAll('.muted').forEach(g=>g.setAttribute('opacity','.08'));return new Blob([new XMLSerializer().serializeToString(clone)],{type:'image/svg+xml;charset=utf-8'})}
document.getElementById('svg').onclick=()=>save(svgBlob(),'travel-map.svg');
document.getElementById('png').onclick=async()=>{const status=document.getElementById('status');try{status.textContent='正在导出高清图片…';await document.fonts.ready;const u=URL.createObjectURL(svgBlob()),img=new Image();await new Promise((ok,fail)=>{img.onload=ok;img.onerror=fail;img.src=u});const c=document.createElement('canvas');c.width=poster.viewBox.baseVal.width*2;c.height=poster.viewBox.baseVal.height*2;c.getContext('2d').drawImage(img,0,0,c.width,c.height);URL.revokeObjectURL(u);const blob=await new Promise(ok=>c.toBlob(ok,'image/png'));if(!blob)throw new Error('图片编码失败');save(blob,'travel-map.png');status.textContent='已导出高清 PNG'}catch(e){status.textContent='导出失败：'+e.message}};
if(trip.pois.some(p=>p.coordinate_status==='unresolved'))para('待定位：'+trip.pois.filter(p=>p.coordinate_status==='unresolved').map(p=>p.name).join('、'));
window.__MAP_READY__=false;document.fonts.ready.then(()=>{window.__MAP_READY__=true});
</script></body></html>'''


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('trip',type=Path);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    d=json.loads(args.trip.read_text(encoding='utf-8'))
    gp=Path(d['map']['geojson'])
    if not gp.is_absolute():gp=args.trip.parent/gp
    geo=json.loads(gp.read_text(encoding='utf-8'))
    report=render(d,geo,args.out)
    print(json.dumps({k:v for k,v in report.items() if k!='label_boxes'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
