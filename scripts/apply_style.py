#!/usr/bin/env python3
"""Apply a curated palette to a travel-map-visualizer base SVG and HTML.

This is an explicit color adapter for the original travel map template, not a
general CSS theme engine. Geometry, labels, route identities and geographic
coordinates are retained. Unknown colors are kept and reported in style.json.
Only Python's standard library is required. Always start from an unthemed base.
"""

import argparse
import copy
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


SVG_NS = "http://www.w3.org/2000/svg"
TERRAIN_LABEL = "由公开高程数据计算的地形阴影"
FILTER_ID = "travel-map-palette-terrain"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

# These are the unthemed template's paint values; map by exact source value.
SOURCE_ROLES = {
    "bg": "#f7f5ec #f7f5eb #eef2e4 #fffef6 #fffdf4",
    "ink": "#10170e #171b15 #384432 #23331d #25331d #182213",
    "muted": "#42503a #66765a #516049 #68745e #64715b #5b6754 #526348 #4d5b44 #6e622f #27361f",
    "county": "#496536 #486335 #66844f #4b613c #526942 #b98233 #638247 #638079 #91ab7c",
    "river": "#417f9a #5a7841",
    "card": "#fffef7 #fffdf7",
    "label": "#151a12 #1b2416 #1b2718",
    "must": "#effb32",
    "mustInk": "#17220f",
    "accent": "#327bef #a36e39",
    "road": "#e5d3a0 #b7c1ab",
    "water": "#b9d9df #a5cbd3 #afd1da",
    "minor": "#d8e7c9 #c7d9b8",
}
PAINT_ATTRS = ("fill", "stroke", "stop-color", "color")
NEUTRAL = {"", "none", "inherit", "currentcolor", "transparent"}
SELECTION_SOURCES = ("explicit", "delegated", "inherited", "preview")
GEOMETRY_ATTRS = {
    "d", "points", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r",
    "rx", "ry", "width", "height", "viewBox", "transform", "preserveAspectRatio",
    "href", "{http://www.w3.org/1999/xlink}href", "data-poi", "data-route",
}


def local_name(el):
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def normalize_color(value):
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if value == "white":
        return "#ffffff"
    return value


def invariant_signature(root):
    """Check geometry and human-visible SVG text independently of serialization."""
    return [
        (local_name(el), sorted((k, v) for k, v in el.attrib.items() if k in GEOMETRY_ATTRS),
         el.text if local_name(el) in {"text", "tspan", "title", "desc"} else None)
        for el in root.iter()
        if local_name(el) not in {"defs", "filter", "feColorMatrix", "feComponentTransfer", "feFuncR", "feFuncG", "feFuncB"}
    ]


def recolor_svg(source, palette):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(source, parser=parser)
    if local_name(root) != "svg":
        raise ValueError("poster.svg 的根元素必须是 SVG。")
    if any("data-style-id" in el.attrib for el in root.iter()):
        raise ValueError("输入 SVG 已应用配色（data-style-id）；请从未配色的原始目录生成新方案。")
    if any(el.get("id") == FILTER_ID for el in root.iter()):
        raise ValueError("输入包含保留的地形滤镜 ID，无法安全应用配色。")
    before = invariant_signature(root)
    mapping = {color: palette[role] for role, colors in SOURCE_ROLES.items() for color in colors.split()}
    unknown, preserved_routes = set(), set()
    stats = {"changed_paints": 0, "terrain_images": 0}
    unsupported_styles = []

    def paint(value, tag, in_route):
        normalized = normalize_color(value)
        if normalized in NEUTRAL or normalized.startswith("url("):
            return value
        # A custom route may happen to share a base-map color. Keep it intact;
        # only the template's standard blue route follows the accent palette.
        if in_route and normalized not in {"#327bef", "#ffffff", "#fffef6", "#1b2718"}:
            preserved_routes.add(value)
            if normalized not in mapping:
                unknown.add(value)
            return value
        if normalized == "#e7efdb":
            result = "#F5F8FA" if tag in {"text", "tspan"} else palette["minor"]
        elif normalized == "#ffffff":
            result = ("#F5F8FA" if tag in {"text", "tspan"} else
                      palette["card"] if tag == "rect" else
                      palette["bg"] if tag == "circle" else palette["minor"])
        elif normalized in mapping:
            result = mapping[normalized]
        else:
            unknown.add(value)
            return value
        if result != value:
            stats["changed_paints"] += 1
        return result

    def walk(el, in_route=False, in_terrain=False):
        tag = local_name(el)
        in_route = in_route or "route" in el.get("class", "").split() or "data-route" in el.attrib
        in_terrain = in_terrain or (tag == "g" and el.get("aria-label") == TERRAIN_LABEL)
        for attr in PAINT_ATTRS:
            if attr in el.attrib:
                el.set(attr, paint(el.get(attr), tag, in_route))
        if "style" in el.attrib:
            # Only exact paint declarations are supported; never replace hex
            # text globally (which could corrupt URLs or embedded image data).
            parts = el.get("style").split(";")
            for i, declaration in enumerate(parts):
                if ":" not in declaration:
                    continue
                key, value = declaration.split(":", 1)
                if key.strip().lower() in PAINT_ATTRS:
                    parts[i] = key + ":" + paint(value.strip(), tag, in_route)
            el.set("style", ";".join(parts))
        if tag == "style":
            # Leave stylesheets intact: this adapter does not resolve CSS
            # selectors, specificity, variables, inheritance or !important.
            unsupported_styles.append("SVG 内嵌样式表原样保留，未执行 CSS 主题解析。")
            unknown.update(re.findall(r"#[0-9a-fA-F]{3,8}\b", el.text or ""))
        if tag == "image" and in_terrain:
            if el.get("filter"):
                unsupported_styles.append("地形 image 已含滤镜，已保留原滤镜且跳过该图片重着色。")
            else:
                el.set("filter", "url(#" + FILTER_ID + ")")
                el.set("opacity", ".72")
                stats["terrain_images"] += 1
        for child in el:
            walk(child, in_route, in_terrain)

    walk(root)
    if stats["terrain_images"]:
        defs = root.find("{" + SVG_NS + "}defs")
        if defs is None:
            defs = ET.Element("{" + SVG_NS + "}defs")
            root.insert(0, defs)
        filt = ET.SubElement(defs, "{" + SVG_NS + "}filter", {
            "id": FILTER_ID, "color-interpolation-filters": "sRGB"})
        ET.SubElement(filt, "{" + SVG_NS + "}feColorMatrix", {"type": "saturate", "values": "0"})
        transfer = ET.SubElement(filt, "{" + SVG_NS + "}feComponentTransfer")
        for channel, start in zip("RGB", (1, 3, 5)):
            low = int(palette["relief"][start:start + 2], 16) / 255
            high = int(palette["bg"][start:start + 2], 16) / 255
            ET.SubElement(transfer, "{" + SVG_NS + "}feFunc" + channel, {
                "type": "linear", "slope": str(high - low), "intercept": str(low)})
    root.set("data-style-id", palette["id"])
    if invariant_signature(root) != before:
        raise ValueError("配色操作意外修改了地图几何或文字；已停止输出。")
    stats.update({"unknown_colors_preserved": sorted(unknown),
                  "custom_route_colors_preserved": sorted(preserved_routes),
                  "warnings": sorted(set(unsupported_styles)),
                  "geometry_and_text_unchanged": True})
    return ET.tostring(root, encoding="unicode"), stats


class PageSpans(HTMLParser):
    """Locate the poster SVG and JSON script without rewriting the HTML."""

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        for match in re.finditer("\n", source):
            self.offsets.append(match.end())
        self.in_poster = False
        self.svg_depth = 0
        self.svg_start = None
        self.svg_spans = []
        self.trip_start = None
        self.trip_spans = []
        self.head_end = None
        self.feed(source)

    def char_offset(self):
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "poster":
            self.in_poster = True
        if tag == "svg" and (self.in_poster or self.svg_depth):
            if self.svg_depth == 0:
                self.svg_start = self.char_offset()
            self.svg_depth += 1
        if tag == "script" and attrs.get("id") == "trip-data":
            self.trip_start = self.char_offset() + len(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag == "svg" and self.svg_depth:
            self.svg_depth -= 1
            if self.svg_depth == 0:
                end = self.source.find(">", self.char_offset()) + 1
                self.svg_spans.append((self.svg_start, end))
                self.in_poster = False
        if tag == "script" and self.trip_start is not None:
            self.trip_spans.append((self.trip_start, self.char_offset()))
            self.trip_start = None
        if tag == "head":
            self.head_end = self.char_offset()


def outer_css(p):
    return """<style id="travel-map-palette-shell">
body{background:%(bg)s;color:%(ink)s}
.toolbar button,#detail,.day-notes{background:%(card)s;color:%(ink)s}
.toolbar button{border-color:%(road)s}
.day-notes p{color:%(muted)s}
#poster{background:%(bg)s}
a{color:%(accent)s}
button:focus-visible,a:focus-visible{outline-color:%(accent)s}
.poi-marker:focus,.poi-label:focus{filter:drop-shadow(0 0 6px %(accent)s)}
</style>""" % p


def apply_style(input_dir, style, output_dir, selection=None):
    source_dir, target_dir = input_dir.resolve(), output_dir.resolve()
    if source_dir == target_dir:
        raise ValueError("输出目录必须与输入目录不同，不允许覆盖原始地图。")
    palettes_path = Path(__file__).resolve().parents[1] / "assets" / "palettes.json"
    palettes = json.loads(palettes_path.read_text(encoding="utf-8"))
    matches = [p for p in palettes if style == p["id"] or style == p["id"].split("-", 1)[0]]
    if len(matches) != 1:
        raise ValueError("无效 style：" + style + "。可用编号为 01–06，或使用 palettes.json 中的完整 id。")
    palette = matches[0]
    for token in ("bg", "ink", "muted", "card", "label", "accent", "must", "mustInk",
                  "water", "river", "road", "minor", "county", "relief"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", palette.get(token, "")):
            raise ValueError("配色 token 无效：" + token)
    svg_source = (source_dir / "poster.svg").read_text(encoding="utf-8")
    html_source = (source_dir / "index.html").read_text(encoding="utf-8")
    svg, report = recolor_svg(svg_source, palette)
    spans = PageSpans(html_source)
    if len(spans.svg_spans) != 1 or spans.head_end is None:
        raise ValueError("HTML 必须有一个 #poster 内的主 SVG 和完整 head；未知模板不会猜测替换。")
    if len(spans.trip_spans) > 1:
        raise ValueError("HTML 包含多个 trip-data，无法确定唯一行程数据。")
    embedded_trip = None
    if spans.trip_spans:
        a, b = spans.trip_spans[0]
        embedded_trip = json.loads(html_source[a:b])
    trip_path = source_dir / "trip.json"
    trip = json.loads(trip_path.read_text(encoding="utf-8")) if trip_path.exists() else embedded_trip
    if trip is not None and not isinstance(trip, dict):
        raise ValueError("trip.json / trip-data 必须是 JSON 对象。")
    if trip is not None and embedded_trip is not None and trip != embedded_trip:
        raise ValueError("输入 trip.json 与 HTML trip-data 不一致；请先同步原始数据再应用配色。")
    if selection is not None and selection not in SELECTION_SOURCES:
        raise ValueError("无效 selection：" + str(selection))
    previous_style = trip.get("style", {}) if trip else {}
    if (selection is None and isinstance(previous_style, dict)
            and previous_style.get("id") == palette["id"]
            and previous_style.get("selection") in SELECTION_SOURCES):
        selection = previous_style["selection"]
    if selection is None:
        selection = "unspecified"
        report["warnings"].append("未提供配色选择来源，也没有匹配同一风格的既有记录；selection 已记为 unspecified，不代表用户已确认。")
    record = {"id": palette["id"], "name": palette["name"], "palette": palette,
              "selection": selection, "source_directory": str(source_dir),
              "adapter": "original-travel-map-template-v1"}
    changes = [(spans.svg_spans[0][0], spans.svg_spans[0][1], svg),
               (spans.head_end, spans.head_end, outer_css(palette))]
    if trip is not None:
        trip = copy.deepcopy(trip)
        trip["style"] = record
        # The preview lives in an independent folder. Preserve the same local
        # dataset for rerendering without duplicating a potentially large file.
        map_config = trip.get("map")
        if isinstance(map_config, dict):
            geojson = map_config.get("geojson")
            if (isinstance(geojson, str) and geojson
                    and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", geojson)
                    and not Path(geojson).is_absolute()):
                map_config["geojson"] = str((source_dir / geojson).resolve())
        if spans.trip_spans:
            a, b = spans.trip_spans[0]
            payload = json.dumps(trip, ensure_ascii=False).replace("<", "\\u003c")
            changes.append((a, b, payload))
    page = html_source
    for a, b, replacement in sorted(changes, reverse=True):
        page = page[:a] + replacement + page[b:]
    if target_dir.exists() and any(target_dir.iterdir()):
        raise ValueError("输出目录非空；请选择一个新目录，以免覆盖既有预览。")
    target_dir.mkdir(parents=True, exist_ok=True)
    output_record = dict(record, source_dir=str(source_dir),
                         source_svg_sha256=hashlib.sha256(svg_source.encode()).hexdigest(),
                         validation=report,
                         scope="仅映射已知模板的显式颜色；未知颜色和自定义路线颜色保留。未复制外部资源，输入应为离线自包含 HTML/SVG。")
    (target_dir / "poster.svg").write_text(svg, encoding="utf-8")
    (target_dir / "index.html").write_text(page, encoding="utf-8")
    (target_dir / "style.json").write_text(json.dumps(output_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if trip is not None:
        (target_dir / "trip.json").write_text(json.dumps(trip, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["unknown_colors_preserved"]:
        print("提示：未知颜色已保留：" + ", ".join(report["unknown_colors_preserved"]), file=sys.stderr)
    for warning in report["warnings"]:
        print("提示：" + warning, file=sys.stderr)
    return {"output_dir": str(target_dir), "style": palette["id"], **report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path, help="含未配色 poster.svg 与 index.html 的目录")
    parser.add_argument("--style", required=True, help="01 至 06，或完整风格 id")
    parser.add_argument("--out", required=True, type=Path, help="独立、空的输出目录")
    parser.add_argument("--selection", choices=SELECTION_SOURCES,
                        help="风格选择来源；未提供时继承同一风格的既有记录，否则记为 unspecified")
    args = parser.parse_args()
    try:
        result = apply_style(args.input_dir, args.style, args.out, args.selection)
    except (ValueError, OSError, ET.ParseError) as exc:
        parser.exit(2, "配色失败：" + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
