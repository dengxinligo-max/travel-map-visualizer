# 导出 HTML 与 PNG

HTML 应是自包含文件：底图、字体、图标等使用内联 SVG、内联样式或 data URL；`#poster` 包含要导出的整张图，操作按钮放在 `#poster` 外。渲染完成时设置 `window.__MAP_READY__ = true`。地图需提前下载并嵌入；导出过程不会访问网络。

## 一次性准备

在你的工作目录安装 Playwright 及其 Chromium：

```sh
npm install playwright
npx playwright install chromium
```

如果环境已提供 Playwright，无需重复安装。通过 `PLAYWRIGHT_MODULE_PATH` 指定 `playwright` 包目录或入口文件；脚本也会从当前工作目录、脚本目录解析模块。优先使用 Playwright 的 Chromium；其不可用时，仅尝试已有的常见 Chrome/Chromium 安装路径，也可显式传入 `--browser /absolute/path/to/browser`。

## 导出

```sh
node /absolute/path/to/travel-map-visualizer/scripts/export_png.mjs \
  /absolute/path/to/guide.html /absolute/path/to/guide.png \
  --width 1080 --scale 2
```

默认以 1080 CSS 像素宽、2 倍像素密度导出，即 PNG 宽度 2160 像素。高度跟随 `#poster` 内 SVG 的 `viewBox` 比例。例如 1080 × 1440 海报输出 2160 × 2880 PNG；长图无需写死高度。`--timeout 30000` 控制就绪检查的等待时限，允许范围 1000–120000 毫秒。

脚本等待地图就绪标志、字体加载、HTML/SVG 图片解码与两帧布局，然后仅截取 `#poster`。外部网络资源、加载失败、页面脚本错误、空图片、零尺寸以及异常 PNG 尺寸都会使导出失败。成功时同时生成 `guide.png.report.json`，记录 CSS/PNG 尺寸、浏览器与字体检查结果。

中文检查会抽样文本，检查字体加载状态，用字形像素比对寻找可能缺字，并尽量通过 Chromium 获取实际使用的字体。字形比对只是启发式检查，不能替代人工检查。交付前必须打开 PNG，确认中文无方框、标注不重叠、连线不断裂、来源署名完整、页边不裁字。`platformFonts` 可能因浏览器协议支持差异为空，不能据此宣称字体已认证。

若中文缺字，在生成 HTML 时嵌入已授权的中文字体，或使用当前机器已安装且覆盖相应字符的中文字体；不要忽略错误后交付。优先打包字体以减少不同设备的排版变化。

参考：[Playwright 元素截图](https://playwright.dev/docs/screenshots#element-screenshot)、[浏览器启动与 executablePath](https://playwright.dev/docs/api/class-browsertype#browser-type-launch)。

## 浏览器不可用时的本地图片路径

如果环境无法启动浏览器，可将同一个 `poster.svg` 用本地 SVG 引擎转 PNG：

```sh
npm install sharp
node scripts/export_svg_png.mjs work/poster.svg work/poster.png --scale 2
```

也可将 `SHARP_MODULE_PATH` 指向已有 `sharp` 包。此工具不启动浏览器，不请求网络，只处理无外部图片/字体引用的 SVG；允许已嵌入为 `data:` URL 的地形图片。它可以完成图片交付，但不能代替 HTML 交互验证；在报告中分别记录“PNG 已生成”和“浏览器测试未执行”。必须打开最终 PNG，检查中文缺字、字宽与排版差异。
