// Development-only helpers. Browser artifacts stay outside business data/.
const fs = require('node:fs');
const path = require('node:path');

function playwright() {
  if (process.env.PLAYWRIGHT_MODULE_PATH) return require(process.env.PLAYWRIGHT_MODULE_PATH);
  try { return require('playwright'); } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    const bundled = path.join(process.env.USERPROFILE || process.env.HOME || '',
      '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
    if (fs.existsSync(bundled)) return require(bundled);
    throw new Error('Install Playwright in the development environment or set PLAYWRIGHT_MODULE_PATH.');
  }
}

function outputDir(name) {
  const root = process.env.QA_OUTPUT_DIR || path.resolve(__dirname, '../../tmp/qa');
  const result = path.resolve(root, name);
  fs.mkdirSync(result, { recursive: true });
  return result;
}

module.exports = { chromium: playwright().chromium, outputDir };
