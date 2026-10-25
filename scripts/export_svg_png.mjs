#!/usr/bin/env node
/** Optional native SVG rasterization when a browser is unavailable. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const args=process.argv.slice(2);
if(args.length<2||args.includes('--help')){
 console.log('Usage: node export_svg_png.mjs input.svg output.png [--scale 2]\nRequires sharp; SHARP_MODULE_PATH may point to an installed sharp package.');process.exit(args.includes('--help')?0:1);
}
const [input,output]=args;
const scale=args.includes('--scale')?Number(args[args.indexOf('--scale')+1]):2;
if(!Number.isFinite(scale)||scale<1||scale>4)throw new Error('scale must be 1..4');
const require=createRequire(path.join(process.cwd(),'travel-map-loader.cjs'));
let sharp;
try{sharp=require(process.env.SHARP_MODULE_PATH||'sharp')}catch{throw new Error('Install sharp locally with npm install sharp, or set SHARP_MODULE_PATH to its installed package.');}
const source=await fs.readFile(input,'utf8');
if(!source.includes('<svg'))throw new Error('Input must be SVG');
if(/<image\b[^>]*(?:href|xlink:href)\s*=\s*["'](?:https?:|file:)|@import|@font-face|url\(\s*["']?(?:https?:|file:)/i.test(source))throw new Error('Only self-contained SVG supported; embed or remove external images/fonts before native rendering.');
const metadata=await sharp(Buffer.from(source)).metadata();
if(!metadata.width||!metadata.height)throw new Error('SVG needs explicit pixel dimensions');
await fs.mkdir(path.dirname(path.resolve(output)),{recursive:true});
await sharp(Buffer.from(source),{density:72*scale}).png().toFile(output);
const actual=await sharp(output).metadata();
if(actual.width!==Math.round(metadata.width*scale)||actual.height!==Math.round(metadata.height*scale))throw new Error('PNG dimensions mismatch');
const report={status:'rasterized-needs-visual-review',renderer:'sharp/librsvg',input:path.resolve(input),output:path.resolve(output),width:actual.width,height:actual.height,scale,notes:['Native SVG rendering is not a browser interaction test.','Inspect Chinese glyphs and text layout before delivery.']};
await fs.writeFile(output+'.report.json',JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
