# 公开地图数据与坐标核验

资料核验日期：2026-10-19。运行时若服务政策或可用性发生变化，以提供方当前页面为准。Skill 包不附带地图文件。这里的下载器在每次任务中按用户区域现查现下，仅请求一小片矢量数据；生成的 HTML 使用已下载的数据，不把公共接口当成浏览器地图后台。

## 来源选择

| 场景 | 公开来源 | 使用方式与边界 |
|---|---|---|
| 城市步行、街道、湖泊、公园 | [OpenStreetMap / Overpass 官方手册](https://dev.overpass-api.de/overpass-doc/en/) | 用附带脚本下载一个有限范围的几何数据，再自行渲染。数据通常较细，但不同地区完整度不同。 |
| 较大的省域、自驾地图或重复使用 | [Geofabrik 数据下载](https://download.geofabrik.de/) | 下载匹配地区的 OSM PBF 或可用的 GeoPackage，使用 GIS 工具离线裁剪、简化并转成 GeoJSON。不要把全国拆成网格反复查询公共 Overpass。 |
| 国家、跨省概览和浅色背景 | [Natural Earth 1:10m 矢量](https://www.naturalearthdata.com/downloads/10m-physical-vectors/) | 适合概览河流、海岸线、湖泊；1:10m 表示 1:1000 万比例尺，不是 10 米分辨率，不适合城市街区。 |
| 大范围山地阴影 | [Natural Earth 地形阴影下载](https://www.naturalearthdata.com/downloads/10m-raster-data/10m-shaded-relief/) | 只作大范围地形背景；裁剪、重投影和配准后再叠加，不能把图片拉伸后当成准确地图。城市级山体细节需要更精细且许可明确的 DEM。 |

Natural Earth 声明其地图数据属于公有领域，仍建议在交付中注明来源：[使用条款](https://www.naturalearthdata.com/about/terms-of-use/)。OSM 数据采用 ODbL，图片和 HTML 都应有清晰署名；可点击页面链接到版权页，导出的图片保留可读来源文字和 URL。数据文件包含许可元数据：[OSM 版权和许可](https://www.openstreetmap.org/copyright)。修改后分发数据库需遵守其适用许可要求，不能用地图署名代替数据库许可说明。

## 下载脚本

仅需 Python 3 标准库：

```sh
python3 scripts/fetch_map.py \
  --bbox 120.10,30.20,120.19,30.28 \
  --profile city \
  --out work/map.geojson
```

- `--bbox` 顺序固定为 **西经度,南纬度,东经度,北纬度**，坐标为 WGS84。包含负数时可写 `--bbox=-0.2,51.4,0.1,51.6`。
- `city` 包含街道、步道、水域、公园、部分边界、地名、具名旅游和历史景点候选。`region` 只取较主要道路，并省去旅游候选，减少大区域复杂度。
- `--endpoint https://…/api/interpreter` 可指定有使用许可的其他 Overpass 服务或自建服务。默认 `https://overpass-api.de/api/interpreter`。不要自动轮换多个公共节点来规避限流。
- `--user-agent 'TravelMap/1.0 (https://your-real-contact-page.example)'` 可提供真实应用名称和联系地址；示例地址必须替换，不能伪造其他应用身份。
- `--input downloaded-overpass.json` 从已有 Overpass JSON 转换，无需网络。输入应来自相同范围、含 `out geom` 的完整输出；只有中心点的原始 JSON 无法生成真实底图。
- 默认每次请求网络，**不自动复用以前的地图数据**。`--use-cache` 可明确选择复用 7 天内的相同查询。旧文件存在不等于此次成功，要检查退出码和 `.metadata.json` 的 `status`。下载时间和地图本身的更新时间分别记录；不要承诺公共地图数据是即时更新的。

输出 `map.geojson`，同时保留 `map.raw.json` 和 `map.metadata.json`。后者记录查询、范围、时间、接口、OSM 数据时间、图层数量、跳过的几何和许可。原始响应不应进前端；交付 HTML 只嵌入所需几何与可显示来源。

脚本使用以下自身保护上限（不是服务方承诺）：城市 ≤ 2500 km²，区域 ≤ 50000 km²；任一方向跨度 ≤ 12°；响应 ≤ 32 MiB；查询声明 45 秒和 64 MiB，网络读取超时 75 秒。最多重试一次；若服务要求超过 30 秒的等待，停止并稍后再试。扩大任务应使用区域数据包和离线 GIS，而非移除限制。公共服务可能限流或拒绝复杂请求：[Overpass 公共资源说明](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)。

## 数据接口与几何处理

GeoJSON 使用 `[longitude, latitude]`。`properties.layer` 为 `road`、`water`、`green`、`boundary`、`place` 或 `poi`；还保留 `name`、`highway`、`osm_id` 和 `source_url` 等属性。`metadata.attribution`、`attribution_url`、`license_url` 可直接进入来源栏。顶层 GeoJSON bbox 表示实际几何范围，`metadata.bbox` 表示用户请求的视图范围。离线导入的原始下载时间未知时 `retrieved_at_utc` 为 null，不能把转换时间冒充获取时间。

水域和绿地使用闭合 Polygon 或 MultiPolygon。脚本会拼接 multipolygon relation 的外环及内环，支持反向 member way 并保留洞；不完整、不闭合、孤立洞、嵌套关系等不强行补齐。常规数据以来源拓扑为准；该轻量转换器不代替专业 GIS 的完整几何有效性检测。被有效关系覆盖的相同图层 member way 不会再次覆盖洞。不得把 `center` 当轮廓，也不得按包围盒画一个矩形冒充湖泊、城区或景区。[Overpass 几何结构说明](https://dev.overpass-api.de/overpass-doc/en/full_data/osm_types.html) 与 [输出格式说明](https://dev.overpass-api.de/overpass-doc/en/targets/formats.html) 解释了原始 JSON 和 GeoJSON 的区别。

只有点、没有任何可用道路或区域几何时，脚本会失败，不生成“空白地图已成功”的假象。原始数据带运行失败 `remark` 时也拒绝转换。若少量几何被跳过，查看 `metadata.skipped` 并检查是否影响主要湖泊或旅游区域；关键要素缺失就缩小范围、取完整许可数据或明确降级，不手画假地理。

## 从攻略文字找到景点

1. 从文字抽取城市、景点名、别名、行程天数和交通方式；“西湖”“古城”等同名词必须结合目的地消歧。
2. 在已下载的 `layer=poi` 候选里匹配名称和别名，并保留 `osm_id` / `source_url`。下载器的候选只是名称和位置线索，不能直接标成“已核验”。
3. 用景区、政府文旅、博物馆等官方网站核对目的地和地址。用公开地图对象几何或用户提供的可靠坐标核对实际位置，不凭网页文字中的地址推算精确经纬度。
4. `coordinate_precision=osm_node` 是 OSM 点位；`osm_bbox_center` 是 OSM 区域的包围盒中心，**不保证是入口**。路线需要入口时另外查证；无法查到时保留近似说明。
5. 模糊或缺失的地点进入待核对清单，不默默放到城市中心。官方文旅页面可证实名称/地址，坐标来源单独记录；不要把“名称存在”写成“坐标准确”。

本技能不内置公共 `nominatim.openstreetmap.org` 地名搜索。其现行政策对自动生成的通用搜索服务有限制；单线程、缓存和低速不能自动消除用途限制。确需地理编码时，选择用途获准的服务或自建服务，保留提供方、坐标系和时间；认真阅读 [Nominatim 使用政策](https://operations.osmfoundation.org/policies/nominatim/) 后再决定。不要在导出的 HTML 中增加调用公共 Nominatim 的通用搜索框。

## 交付时的地图约束

- 使用真实几何底图，并保持统一投影；不得把 GCJ-02、BD-09 坐标直接叠到 WGS84 数据上。来源不明确的坐标先确认。
- 攻略的先后顺序可以用带箭头的示意连线；没有道路导航计算时，标明“行程示意，非导航路线”。不得把点间直线冒充实际驾车路线或计算出真实行驶公里数/时间。
- 区域自驾优先获取可用于离线路由的 OSM 道路几何，保留 `highway`、`ref`、`oneway`、`access`、`motor_vehicle`。`scripts/route_local.py` 使用这些字段生成分段道路中心线；它不是完整导航引擎，未覆盖所有转向限制、实时封路和景区内部交通。若使用在线路由服务，先核对其当期条款、坐标系、车辆类型及返回几何，下载到本次输出目录，不让交付 HTML 依赖在线查询。
- 沿途县级行政区优先查官方资料与有许可的边界/地名数据。OSM `place` 点通常是县城代表点，不是县界；只有拿到可信边界几何才能绘边界线。河流和国道标注分别绑定具名水系与道路 `ref`，出处和获取时间写入数据。
- 行政界线是底图参考。跨境、争议边界或准备公开发布的中国行政区地图，应核对适用的官方标准地图来源与使用要求；不把 OSM 或 Natural Earth 的界线当权威边界结论。
- 公共 OSM 标准栅格瓦片禁止批量下载、预取或离线包功能。本技能使用公开地理数据自行渲染，而非抓取瓦片：[OSM 瓦片政策](https://operations.osmfoundation.org/policies/tiles/)。
- 不需要在线地图瓦片也能打开已生成 HTML。网络故障时可用已下载并明确记录时间的数据；没有真实数据时先标记缺失，不能用程序噪声或随手曲线伪装山川道路。

## 备用公开接口

原服务不可用时，可以核验并明确选择一个备用来源：[Private.coffee 服务说明](https://overpass.private.coffee/) 和其 `https://overpass.private.coffee/api/interpreter`。不要以换节点规避限流。若该区域的在线服务持续不可用，改用下载许可明确的区域数据包；两种方法均失败就报告数据缺失，不能制造地图。Natural Earth 官方维护的 [GeoJSON 目录](https://github.com/nvkelso/natural-earth-vector/tree/master/geojson) 可用于较大区域概览，使用时下载匹配图层并离线裁剪；不要将全球数据打进 Skill 包。
