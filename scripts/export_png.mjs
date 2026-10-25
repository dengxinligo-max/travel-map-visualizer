#!/usr/bin/env node
/** Export the self-contained #poster element; does not contact the network. */
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const HELP = `Usage: node export_png.mjs input.html output.png [options]
  --scale NUMBER    Device scale factor; default 2 (1080 CSS px -> 2160 PNG px)
  --width NUMBER    Poster width in CSS pixels; default 1080
  --timeout NUMBER Readiness timeout in milliseconds; default 30000
  --browser PATH   Optional Chromium/Chrome executable

Install: npm install playwright && npx playwright install chromium
PLAYWRIGHT_MODULE_PATH may point to the playwright package directory or entry file.
The HTML must contain #poster and set window.__MAP_READY__ = true when complete.`;

function options(argv) {
  if (argv.includes('--help') || argv.includes('-h')) {
    console.log(HELP);
    process.exit(0);
  }
  const result = { scale: 2, width: 1080, timeout: 30000, browser: undefined };
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith('--')) { positional.push(arg); continue; }
    const key = arg.slice(2);
    if (!Object.hasOwn(result, key) || !argv[i + 1] || argv[i + 1].startsWith('--')) {
      throw new Error(`Unknown or incomplete option: ${arg}\n${HELP}`);
    }
    result[key] = key === 'browser' ? argv[++i] : Number(argv[++i]);
  }
  if (positional.length !== 2) throw new Error(HELP);
  if (!Number.isFinite(result.scale) || result.scale < 0.25 || result.scale > 4) throw new Error('--scale must be between 0.25 and 4.');
  if (!Number.isInteger(result.width) || result.width < 320 || result.width > 4096) throw new Error('--width must be an integer between 320 and 4096.');
  if (!Number.isInteger(result.timeout) || result.timeout < 1000 || result.timeout > 120000) throw new Error('--timeout must be between 1000 and 120000 ms.');
  result.input = path.resolve(positional[0]);
  result.output = path.resolve(positional[1]);
  if (path.extname(result.output).toLowerCase() !== '.png') throw new Error('Output filename must end in .png.');
  if (result.input === result.output) throw new Error('Input and output must differ.');
  return result;
}

function loadPlaywright() {
  const localRequire = createRequire(import.meta.url);
  const cwdRequire = createRequire(path.join(process.cwd(), '__travel_map_export__.cjs'));
  const candidates = [process.env.PLAYWRIGHT_MODULE_PATH].filter(Boolean);
  const errors = [];
  for (const candidate of candidates) {
    try { return localRequire(path.resolve(candidate)); }
    catch (error) { errors.push(`${candidate}: ${error.message.split('\n')[0]}`); }
  }
  for (const require of [cwdRequire, localRequire]) {
    try { return require('playwright'); }
    catch (error) { errors.push(error.message.split('\n')[0]); }
  }
  throw new Error(`Playwright is unavailable. Run npm install playwright and npx playwright install chromium, or set PLAYWRIGHT_MODULE_PATH.\n${errors.join('\n')}`);
}

async function launch(chromium, executable) {
  if (executable) {
    const resolved = path.resolve(executable);
    if (!existsSync(resolved)) throw new Error(`Browser executable does not exist: ${resolved}`);
    return { browser: await chromium.launch({ headless: true, executablePath: resolved }), source: resolved };
  }
  try { return { browser: await chromium.launch({ headless: true }), source: 'Playwright Chromium' }; }
  catch (original) {
    const known = process.platform === 'darwin'
      ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium']
      : process.platform === 'win32'
        ? [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean).map(root => path.join(root, 'Google', 'Chrome', 'Application', 'chrome.exe'))
        : ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
    const fallbackErrors = [];
    for (const candidate of known.filter(existsSync)) {
      try { return { browser: await chromium.launch({ headless: true, executablePath: candidate }), source: candidate }; }
      catch (error) { fallbackErrors.push(`${candidate}: ${error.message.split('\n')[0]}`); }
    }
    throw new Error(`Cannot launch Chromium. Run npx playwright install chromium or supply --browser.\n${original.message}\n${fallbackErrors.join('\n')}`);
  }
}

async function fontAudit(page, context) {
  const metrics = await page.evaluate(() => {
    const poster = document.querySelector('#poster');
    const chinese = /[\u3400-\u9fff]/u;
    const samples = [...poster.querySelectorAll('*')]
      .filter(el => !el.children.length && chinese.test(el.textContent || '') && el.getBoundingClientRect().width > 0)
      .slice(0, 12);
    const canvas = document.createElement('canvas');
    canvas.width = 96; canvas.height = 80;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const hashGlyph = char => {
      ctx.clearRect(0, 0, 96, 80);
      ctx.fillText(char, 8, 58);
      let hash = 2166136261;
      const pixels = ctx.getImageData(0, 0, 96, 80).data;
      for (let i = 3; i < pixels.length; i += 4) hash = Math.imul(hash ^ pixels[i], 16777619) >>> 0;
      return hash;
    };
    return samples.map((el, i) => {
      el.setAttribute('data-export-font-audit', String(i));
      const css = getComputedStyle(el);
      const chars = [...new Set([...el.textContent].filter(ch => chinese.test(ch)))].slice(0, 8);
      ctx.font = `${css.fontStyle} ${css.fontWeight} 48px ${css.fontFamily}`;
      const missingHash = hashGlyph('\u0378');
      const suspicious = chars.filter(ch => hashGlyph(ch) === missingHash);
      return { sample: el.textContent.trim().slice(0, 40), family: css.fontFamily,
        loaded: document.fonts.check(`${css.fontWeight} ${css.fontSize} ${css.fontFamily}`, chars.join('')),
        suspiciousMissingGlyphs: suspicious, platformFonts: [] };
    });
  });
  let platformFontAudit = 'available';
  try {
    const session = await context.newCDPSession(page);
    await session.send('DOM.enable');
    await session.send('CSS.enable');
    const { root } = await session.send('DOM.getDocument');
    for (let i = 0; i < metrics.length; i++) {
      const { nodeId } = await session.send('DOM.querySelector', { nodeId: root.nodeId, selector: `[data-export-font-audit="${i}"]` });
      const { fonts } = await session.send('CSS.getPlatformFontsForNode', { nodeId });
      metrics[i].platformFonts = fonts.filter(font => font.glyphCount > 0)
        .map(font => ({ family: font.familyName, glyphCount: font.glyphCount, custom: font.isCustomFont }));
    }
    await session.detach();
  } catch (error) { platformFontAudit = `unavailable: ${error.message.split('\n')[0]}`; }
  await page.evaluate(() => document.querySelectorAll('[data-export-font-audit]').forEach(el => el.removeAttribute('data-export-font-audit')));
  const failures = metrics.filter(item => !item.loaded || item.suspiciousMissingGlyphs.length);
  if (failures.length) throw new Error(`Chinese font readiness or missing-glyph check failed: ${JSON.stringify(failures)}`);
  return { platformFontAudit, samples: metrics, note: 'Glyph checks are heuristic; visually inspect the PNG for missing characters and clipping.' };
}

async function main() {
  const opts = options(process.argv.slice(2));
  await fs.access(opts.input);
  const { chromium } = loadPlaywright();
  const { browser, source } = await launch(chromium, opts.browser);
  try {
    const context = await browser.newContext({ viewport: { width: opts.width, height: 1440 }, deviceScaleFactor: opts.scale, locale: 'zh-CN', reducedMotion: 'reduce' });
    const failures = [];
    const inputURL = pathToFileURL(opts.input).href;
    await context.route('**/*', route => {
      const request = route.request();
      const url = request.url();
      if (url.startsWith('data:') || url.startsWith('blob:') || (request.isNavigationRequest() && url.split('#')[0] === inputURL)) return route.continue();
      failures.push(`External resource is not embedded: ${url}`);
      return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(opts.timeout);
    page.on('requestfailed', request => failures.push(`${request.url()}: ${request.failure()?.errorText || 'request failed'}`));
    page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`); });
    page.on('pageerror', error => failures.push(`Page script: ${error.message}`));
    await page.goto(inputURL, { waitUntil: 'load', timeout: opts.timeout });
    await page.locator('#poster').waitFor({ state: 'visible' });
    await page.waitForFunction(() => window.__MAP_READY__ === true, undefined, { timeout: opts.timeout });
    await page.addStyleTag({ content: `#poster{width:${opts.width}px!important;max-width:none!important;height:auto!important;transform:none!important}#poster>svg{width:100%!important;height:auto!important;display:block}*{animation:none!important;transition:none!important;caret-color:transparent!important}` });
    await page.evaluate(async timeout => {
      const ready = async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(async image => {
          if (!image.complete) await new Promise((resolve, reject) => { image.addEventListener('load', resolve, { once: true }); image.addEventListener('error', () => reject(new Error(`Image failed: ${image.src.slice(0, 160)}`)), { once: true }); });
          if (!image.naturalWidth || !image.naturalHeight) throw new Error(`Empty image: ${image.src.slice(0, 160)}`);
          if (image.decode) await image.decode();
        }));
        await Promise.all([...document.querySelectorAll('#poster svg image')].map(element => new Promise((resolve, reject) => {
          const src = element.getAttribute('href') || element.getAttribute('xlink:href');
          if (!src) { reject(new Error('SVG image has no href')); return; }
          const image = new Image();
          image.onload = () => image.naturalWidth && image.naturalHeight ? resolve() : reject(new Error('Empty SVG image'));
          image.onerror = () => reject(new Error(`SVG image failed: ${src.slice(0, 160)}`));
          image.src = src;
        })));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      };
      let timer;
      try { await Promise.race([ready(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Font/image readiness timed out')), timeout); })]); }
      finally { clearTimeout(timer); }
    }, opts.timeout);
    const fonts = await fontAudit(page, context);
    const poster = page.locator('#poster');
    const bounds = await poster.boundingBox();
    if (!bounds || bounds.width <= 0 || bounds.height <= 0) throw new Error('#poster has zero dimensions.');
    if (Math.abs(bounds.width - opts.width) > 1) throw new Error(`Poster width is ${bounds.width}, expected ${opts.width}.`);
    if (bounds.width * bounds.height * opts.scale ** 2 > 100_000_000) throw new Error('Export exceeds 100 megapixels; reduce --scale or split the poster.');
    if (failures.length) throw new Error(`Resource/readiness errors:\n${[...new Set(failures)].join('\n')}`);
    await fs.mkdir(path.dirname(opts.output), { recursive: true });
    const png = await poster.screenshot({ type: 'png', animations: 'disabled', scale: 'device', timeout: opts.timeout });
    if (failures.length) throw new Error(`Resource errors during capture:\n${[...new Set(failures)].join('\n')}`);
    const width = png.readUInt32BE(16), height = png.readUInt32BE(20);
    if (width <= 0 || height <= 0 || Math.abs(width - bounds.width * opts.scale) > 2 || Math.abs(height - bounds.height * opts.scale) > 2) throw new Error('PNG dimensions do not match the poster and scale.');
    await fs.writeFile(opts.output, png);
    const report = { input: opts.input, output: opts.output, browser: source, css: { width: bounds.width, height: bounds.height }, png: { width, height, bytes: png.length }, scale: opts.scale, resourceErrors: [], fonts };
    const reportPath = `${opts.output}.report.json`;
    await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
    console.log(JSON.stringify({ output: opts.output, width, height, bytes: png.length, report: reportPath, chineseTextSamples: fonts.samples.length }, null, 2));
  } finally { await browser.close(); }
}

main().catch(error => { console.error(`Export failed: ${error.message}`); process.exitCode = 1; });
