const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

function loadTypeScript(file) {
  const source = fs.readFileSync(file, 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const adapter = { exports: {} }
  new Function('exports', 'module', 'require', compiled)(adapter.exports, adapter, id => {
    if (!id.startsWith('.')) return require(id)
    return loadTypeScript(path.resolve(path.dirname(file), `${id}.ts`))
  })
  return adapter.exports
}
module.exports = { loadTypeScript }
