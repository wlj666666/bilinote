import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ESLint } from 'eslint'
import ts from 'typescript'
import { newDiagnostics } from './ci-baseline.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const baselinePath = path.join(root, 'ci-diagnostics-baseline.json')

function relative(file) {
  return path.relative(root, file).split(path.sep).join('/')
}

function typeDiagnostics() {
  const diagnostics = []
  for (const configName of ['tsconfig.app.json', 'tsconfig.node.json']) {
    const configPath = path.join(root, configName)
    const config = ts.readConfigFile(configPath, ts.sys.readFile)
    if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'))
    const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root)
    if (parsed.errors.length) {
      throw new Error(parsed.errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'))
    }
    const program = ts.createProgram(parsed.fileNames, parsed.options)
    for (const diagnostic of ts.getPreEmitDiagnostics(program)) {
      if (diagnostic.category !== ts.DiagnosticCategory.Error) continue
      diagnostics.push({
        file: diagnostic.file ? relative(diagnostic.file.fileName) : configName,
        code: `TS${diagnostic.code}`,
        message: ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n').replaceAll(root, '<frontend>/'),
        source: diagnostic.file && diagnostic.start !== undefined
          ? diagnostic.file.text.slice(diagnostic.start, diagnostic.start + (diagnostic.length ?? 0))
          : '',
      })
    }
  }
  return diagnostics
}

async function lintDiagnostics() {
  const results = await new ESLint({ cwd: root }).lintFiles(['.'])
  const diagnostics = []
  let warnings = 0
  for (const result of results) {
    warnings += result.warningCount
    for (const message of result.messages) {
      if (message.fatal) throw new Error(`${relative(result.filePath)}: ${message.message}`)
      if (message.severity !== 2) continue
      diagnostics.push({
        file: relative(result.filePath),
        code: message.ruleId,
        message: message.message,
        source: (result.source ?? fs.readFileSync(result.filePath, 'utf8')).split(/\r?\n/)[message.line - 1]?.trim() ?? '',
      })
    }
  }
  console.log(`ESLint warnings: ${warnings} (reported, not blocking)`)
  return diagnostics
}

async function main() {
  const args = process.argv.slice(2)
  if (args.some(arg => arg !== '--write-baseline')) throw new Error('Only --write-baseline is supported')
  const current = { version: 1, typecheck: typeDiagnostics(), lint: await lintDiagnostics() }
  if (args.includes('--write-baseline')) {
    fs.writeFileSync(baselinePath, `${JSON.stringify(current, null, 2)}\n`)
    console.log(`Baseline written: ${current.typecheck.length} type errors, ${current.lint.length} lint errors`)
    return
  }
  const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8'))
  if (baseline.version !== 1 || !Array.isArray(baseline.typecheck) || !Array.isArray(baseline.lint)) {
    throw new Error('Invalid diagnostic baseline')
  }
  for (const kind of ['typecheck', 'lint']) {
    const added = newDiagnostics(current[kind], baseline[kind])
    console.log(`${kind}: ${current[kind].length} total errors, ${added.length} new errors`)
    for (const diagnostic of added) {
      console.error(`${diagnostic.file} ${diagnostic.code}: ${diagnostic.message}\n  ${diagnostic.source}`)
    }
    if (added.length) process.exitCode = 1
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    console.error(error)
    process.exitCode = 1
  })
}
