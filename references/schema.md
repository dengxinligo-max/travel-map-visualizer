# 输入结构

把用户原文提炼为 `trip.json`。这是由调用 Skill 的模型完成的语义解析，不要求用户手写 JSON。附带渲染器消费结构化数据，不是自然语言理解模型。

## 顶层

| 字段 | 含义 |
|---|---|
| `version` | 固定为 `1` |
| `title`, `subtitle` | 主标题、副标题；建议主标题 5–9 个汉字 |
| `region`, `eyebrow`, `edition` | 顶部地区、栏目名和版本说明，可选 |
| `mode` | `city` / `region` / `multi-route`；用于选择底图细节与内容组织，基础渲染器共享同一版式骨架 |
| `height` | 默认 1440，可增至 2400；宽固定 1080 |
| `style` | 已选主题：`id`、`name`、`selection`；自定义另存 `tokens`，详见 [风格选择](styles.md) |
| `map` | 地图文件、坐标系、范围及署名，见下 |
| `pois` | 景点数组 |
| `routes` | 0–4 条路线；超出时拆图 |
| `tips` | 简短提示数组，主图显示前两条，完整信息留在数据中 |
| `area_labels` | 可选区域文字，`name` + WGS84 `coord`；仅用于地名，不生成边界 |
| `map_labels` | 可选路书地理标注，含 `name`、`kind`、`coord`、`source`；`kind` 为 `road`、`river`、`county`、`town` 或 `landmark` |
| `assumptions`, `sources` | 推断记录、内容来源，留在可修改的数据内 |

```json
{
  "version": 1,
  "title": "西湖旅行地图",
  "subtitle": "两日慢游 · 把时间留给湖边",
  "region": "浙江 · 杭州",
  "mode": "city",
  "map": {
    "geojson": "map.geojson",
    "crs": "EPSG:4326",
    "bbox": [120.10, 30.20, 120.19, 30.28],
    "attribution": "© OpenStreetMap contributors · ODbL · openstreetmap.org/copyright",
    "source_url": "https://www.openstreetmap.org/copyright",
    "retrieved_at": "填写实际数据获取日期"
  },
  "pois": [],
  "routes": [],
  "tips": []
}
```

`map.geojson` 相对 `trip.json` 所在目录解析。必须是 FeatureCollection；渲染器支持 `properties.layer` 为 `road`、`water`、`green`、`boundary` 的真实 LineString/MultiLineString/Polygon/MultiPolygon。`place`、`poi` 用于候选匹配；只有加入 `pois` 或 `area_labels` 的点才会上图，避免底图标签过密。其他数据源先做字段映射，不要误把其“水域名称”当几何。

`map.bbox` 是画面范围，所有 POI 与实际路径都应在范围内。数据文件实际几何可能伸出查询范围；渲染器会裁剪到画布。使用统一 Web Mercator 投影，不跨日期变更线；遇到跨线或高纬极地任务时换合适的投影实现。

## 景点

```json
{
  "id": "broken-bridge",
  "name": "断桥残雪",
  "coord": null,
  "coordinate_status": "unresolved",
  "coordinate_source": "",
  "priority": "must",
  "day": "D1",
  "fee": "",
  "note": "从湖东北侧开启第一天。",
  "original_text": "原文中对应的句子",
  "label_side": "right"
}
```

- `id` 用唯一的英文字母/数字/下划线/连字符；`name` 可用中文。
- `coord` 是 `[经度,纬度]`，**不是** `[纬度,经度]`。
- `coordinate_status` 为 `verified`、`approximate` 或 `unresolved`。`verified` 表示该位置已核对，不意味着门票、开放信息也已核对。`approximate` 显示“约位”，`unresolved` 不上图且不连线。
- `coordinate_source` 给出实际坐标来源链接/文件依据。不得用“官方”二字替代来源。
- `priority`: `must` 必去、`recommended` 推荐、`optional` 顺路；对应颜色由选定主题的 `must`、`label`、`county` 控制，不能固定为黄/黑/绿。不要把模型自选推荐当成用户必去要求。
- `fee` 可以为空；“免费”“¥120”“5A”等均需用户原文或核验依据。时效信息另记 `sources` 和日期。
- `label_side` 可省略自动分配。若有重叠，先在两侧均衡标签，再调整范围/高度或拆图；不能移动真实标记来迁就排版。
- 每点图面备注建议 15–40 字；完整原文在点选详情保留。

## 路线

```json
{
  "id": "D1",
  "name": "北岸慢走",
  "kind": "schematic",
  "color": "#327bef",
  "poi_ids": ["broken-bridge", "autumn-moon", "lotus"],
  "description": "断桥 → 平湖秋月 → 曲院风荷；按原文顺序游览。"
}
```

- `kind=schematic`：直连已确认的景点，虚线并明示“游览顺序”。中途缺失的点会断线，不会悄悄跳过继续连接。
- `kind=routed` 或 `user_track`：必须额外提供 `geometry: {"type":"LineString","coordinates":[...]}` 和 `source`。坐标须为真实完整路径，且使用相同坐标系；以实线画出。
- `scripts/route_local.py` 可从下载的道路 GeoJSON 生成 `road-route.json`，记录各段 LineString、里程、路号和每个 POI 到最近道路的距离。加 `--trip-out trip-routed.json` 可直接生成待渲染的行程副本；吸附距离大于景区入口可接受距离时，不能把景点坐标接成可驾车道路。该脚本是道路中心线最短路，不验证实时道路状态或完整转向限制。
- `map_labels` 的县级名称应关联官方或已核验的地图来源；行政区代表点不等于边界。没有边界几何时，用区域提示文字，不绘制虚构县界。
- 点序列只是画面说明；不能在没有路线几何与来源的情况下声称“公路导航”。
- 多线路方案可用 `A/B/C/D` 作为 id；`description` 包含天数、重点地区和适合人群，避免过长。

## 输出与报告

`render_map.py` 输出 `index.html`、`poster.svg`、`render-report.json`；`export_png.mjs` 输出 PNG 和旁边的图片检查报告。渲染报告记录地图要素数、点数、未定位点、画幅、重叠警告。生成 SVG 不等于完成视觉验收。

初次试跑：复制 `assets/example-trip.json` 到独立工作文件夹，下载 `map.geojson`，核验并填入景点坐标后运行渲染器。不要把示例里未核验的字段直接改成 verified 来通过检查。
