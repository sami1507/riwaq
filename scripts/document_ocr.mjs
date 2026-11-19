/** Fixed offline OCR CLI. No downloads, code execution, or cloud fallback. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import dns from 'node:dns';
import childProcess from 'node:child_process';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { isMainThread } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';

const runtime = path.resolve(process.env.RIWAQ_DOCUMENT_RUNTIME || 'D:/RiwaqRuntime/documents-v1');
const self = fileURLToPath(import.meta.url);
const deny = () => { throw new Error('Network/process dispatch disabled in fixed OCR worker'); };
globalThis.fetch = async () => deny();
http.request = http.get = https.request = https.get = deny;
net.connect = net.createConnection = net.Socket.prototype.connect = tls.connect = deny;
for (const name of Object.keys(dns)) if (name.startsWith('resolve') || name === 'lookup' || name === 'lookupService') dns[name] = deny;
if (dns.promises) for (const name of Object.keys(dns.promises)) if (name.startsWith('resolve') || name.startsWith('lookup')) dns.promises[name] = async () => deny();
for (const name of ['exec','execFile','spawn','fork','execSync','execFileSync','spawnSync']) childProcess[name] = deny;
syncBuiltinESMExports();

function sha(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function boundedFile(filename, max) {
  const stat = fs.statSync(filename);
  if (!stat.isFile() || stat.size > max) throw new Error('File exceeds allowed size');
  return fs.readFileSync(filename);
}
function localFile(relative) {
  const candidate = path.resolve(runtime, relative);
  const rel = path.relative(runtime, candidate);
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) throw new Error('Runtime path escape');
  const actual = fs.realpathSync(candidate);
  const realRel = path.relative(fs.realpathSync(runtime), actual);
  if (!realRel || realRel.startsWith('..') || path.isAbsolute(realRel)) throw new Error('Runtime link escape');
  return actual;
}
function loadManifest() {
  const bytes = boundedFile(path.join(runtime, 'runtime-manifest.json'), 4 * 1024 ** 2);
  const data = JSON.parse(bytes);
  if (data.version !== 'document-runtime-v1') throw new Error('Unknown document runtime manifest');
  if (data.node_packages?.['tesseract.js']?.version !== '7.0.0' ||
      data.node_packages?.['tesseract.js-core']?.version !== '7.0.0') throw new Error('Unapproved OCR package version');
  return { data, fingerprint: sha(bytes) };
}
function verifyRuntime(manifest, languages) {
  let count = 0;
  for (const [name, record] of Object.entries(manifest.files)) {
    if (!name.startsWith('node_modules/')) continue;
    if (++count > 10000) throw new Error('Runtime file count exceeded');
    const bytes = boundedFile(localFile(name), 64 * 1024 ** 2);
    if (bytes.length !== record.bytes || sha(bytes) !== record.sha256) throw new Error('OCR dependency integrity mismatch: ' + name);
  }
  if (!count) throw new Error('No locked OCR dependency files');
  for (const language of languages) {
    const record = manifest.languages?.[language];
    if (!record) throw new Error('Local OCR language unavailable: ' + language);
    const bytes = boundedFile(localFile('tessdata/' + language + '.traineddata'), 32 * 1024 ** 2);
    if (bytes.length !== record.bytes || sha(bytes) !== record.sha256) throw new Error('Language integrity mismatch');
  }
}
function outputJson(filename, data) {
  const bytes = Buffer.from(JSON.stringify(data), 'utf8');
  if (bytes.length > 4 * 1024 ** 2) throw new Error('OCR JSON exceeds4MiB');
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const temporary = filename + '.part';
  fs.writeFileSync(temporary, bytes, { flag: 'wx' });
  if (fs.existsSync(filename)) { fs.unlinkSync(temporary); throw new Error('Refusing to overwrite existing OCR result'); }
  fs.renameSync(temporary, filename);
}
function parseWords(tsv, width, height) {
  const rows = tsv.split(/\r?\n/);
  const header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext';
  const start = rows[0] === header ? 1 : 0;
  const words = [];
  // The WASM API emits records without the CLI header; retain raw TSV unchanged.
  for (let index = start; index < rows.length; index++) {
    if (!rows[index]) continue;
    const fields = rows[index].split('\t');
    if (fields.length < 12 || !/^[1-5]$/.test(fields[0]) ||
        !fields.slice(1, 10).every(x => /^\d+$/.test(x))) throw new Error('Invalid OCR TSV record');
    if (fields[0] !== '5' || !fields.slice(11).join('\t').trim()) continue;
    const [page_num, block_num, par_num, line_num, word_num, left, top, w, h] = fields.slice(1, 10).map(Number);
    const confidence = Number(fields[10]);
    if (![page_num, block_num, par_num, line_num, word_num, left, top, w, h].every(Number.isSafeInteger) ||
        !Number.isFinite(confidence) || confidence < -1 || confidence > 100 ||
        left + w > width || top + h > height) throw new Error('Out-of-bounds OCR word');
    words.push({ text: fields.slice(11).join('\t'), confidence, left, top, width: w, height: h,
                 page_num, block_num, par_num, line_num, word_num, tsv_row: index + 1 });
    if (words.length > 20000) throw new Error('OCR word count exceeded');
  }
  return words;
}
if (!isMainThread) {
  // Tesseract spawns this module as its worker: guards are installed before its adapter captures fetch.
  const { data } = loadManifest();
  const req = createRequire(localFile('node_modules/tesseract.js/package.json'));
  const core = req.resolve('tesseract.js-core/package.json');
  if (fs.realpathSync(core) !== localFile('node_modules/tesseract.js-core/package.json')) throw new Error('Unexpected core resolution');
  req(localFile('node_modules/tesseract.js/src/worker-script/node/index.js'));
} else {
  let worker, timer, output;
  const started = performance.now();
  try {
    const args = {};
    const argv = process.argv.slice(2);
    if (argv.length !== 6) throw new Error('Use --input PNG --output JSON --languages ara+eng+heb');
    for (let i = 0; i < argv.length; i += 2) {
      if (!['--input', '--output', '--languages'].includes(argv[i]) || args[argv[i]] !== undefined) throw new Error('Invalid fixed OCR arguments');
      args[argv[i]] = argv[i + 1];
    }
    const input = path.resolve(args['--input']); output = path.resolve(args['--output']);
    if (input === output || fs.existsSync(output)) throw new Error('Output must be a new separate file');
    const language = args['--languages'];
    const languages = language.split('+');
    if (!languages.length || languages.length > 3 || new Set(languages).size !== languages.length ||
        languages.some(x => !['ara','eng','heb'].includes(x))) throw new Error('Only ara/eng/heb are configured');
    const image = boundedFile(input, 25 * 1024 ** 2);
    if (image.length < 24 || !image.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) ||
        image.toString('ascii', 12, 16) !== 'IHDR') throw new Error('OCR accepts a normalized PNG only');
    const width = image.readUInt32BE(16), height = image.readUInt32BE(20);
    if (!width || !height || width > 16384 || height > 16384 || width * height > 16000000) throw new Error('OCR pixel limit exceeded');
    timer = setTimeout(() => {
      try { outputJson(output, { success: false, engine: 'tesseract.js', error: 'OCR wall timeout50s', language }); } catch {}
      process.exit(124);
    }, 50000);
    const { data: manifest, fingerprint } = loadManifest();
    verifyRuntime(manifest, languages);
    const req = createRequire(localFile('node_modules/tesseract.js/package.json'));
    const { createWorker, PSM } = req(localFile('node_modules/tesseract.js/src/index.js'));
    worker = await createWorker(language, 1, {
      workerPath: self, corePath: localFile('node_modules/tesseract.js-core/package.json').replace(/[\\/]package\.json$/, ''),
      langPath: path.join(runtime, 'tessdata'), gzip: false, cacheMethod: 'none',
      cachePath: path.join(runtime, 'tessdata'), legacyCore: false, legacyLang: false,
      logger: () => {}, errorHandler: () => {},
    });
    await worker.setParameters({ tessedit_pageseg_mode: PSM.AUTO });
    const result = await worker.recognize(image, { rotateAuto: false }, { text: true, tsv: true });
    const tsv = result.data.tsv, text = result.data.text;
    if (typeof tsv !== 'string' || typeof text !== 'string' ||
        Buffer.byteLength(tsv) > 2 * 1024 ** 2 || Buffer.byteLength(text) > 512 * 1024) throw new Error('Invalid or oversized OCR output');
    const words = parseWords(tsv, width, height);
    await worker.terminate(); worker = null;
    clearTimeout(timer);
    const report = { success: true, engine: 'tesseract.js', engine_version: '7.0.0', core_package_version: '7.0.0',
      language, text_raw: text, tsv, tsv_has_header: tsv.startsWith('level\t'), words, width, height, input_sha256: sha(image), tsv_sha256: sha(Buffer.from(tsv)),
      runtime_manifest_sha256: fingerprint, tessdata_revision: manifest.tessdata_revision, elapsed_seconds: (performance.now() - started) / 1000,
      coordinate_system: 'top-left origin in raw preview pixels', page_segmentation_mode: 3, oem: 1,
      network: 'fetch/http/https/net/tls/dns blocked in main and worker thread; local files only; not an OS firewall',
      warnings: ['OCR text and boxes may contain errors. Engine confidence is not calibrated correctness.'] };
    outputJson(output, report);
    console.log(JSON.stringify({ success: true, words: words.length, elapsed_seconds: report.elapsed_seconds }));
  } catch (error) {
    clearTimeout(timer);
    if (worker) { try { await worker.terminate(); } catch {} }
    if (output && !fs.existsSync(output)) {
      try { outputJson(output, { success: false, engine: 'tesseract.js', error: String(error?.message || error).slice(0, 1000) }); } catch {}
    }
    console.error(String(error?.message || error).slice(0, 1000));
    process.exitCode = 1;
  }
}
