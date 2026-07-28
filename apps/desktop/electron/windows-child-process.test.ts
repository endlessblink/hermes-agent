import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { test } from 'vitest'
import { fileURLToPath } from 'node:url'

const ELECTRON_DIR = path.dirname(fileURLToPath(import.meta.url))

// TODO FIXME these tests all grep source code for specific things. This is an antipattern.
// Tests should NEVER read src, only assert behavior.

function readElectronFile(name) {
  return fs.readFileSync(path.join(ELECTRON_DIR, name), 'utf8').replace(/\r\n/g, '\n')
}

function requireHiddenChildOptions(source, needle) {
  const match = needle instanceof RegExp ? needle.exec(source) : null
  const index = needle instanceof RegExp ? (match?.index ?? -1) : source.indexOf(needle)
  assert.notEqual(index, -1, `missing call site: ${needle}`)
  const snippet = source.slice(index, index + 700)
  assert.match(
    snippet,
    /hiddenWindowsChildOptions\(/,
    `expected ${needle} to wrap child-process options with hiddenWindowsChildOptions`
  )
}

test('desktop background child processes opt into hidden Windows consoles', () => {
  const source = readElectronFile('main.ts')

  assert.match(source, /import \{ hiddenWindowsChildOptions \} from '.\/windows-child-options'/)

  requireHiddenChildOptions(source, "execFileSync(\n          'reg'")
  requireHiddenChildOptions(source, /execFileSync\(\s*pyExe/)
  requireHiddenChildOptions(source, /spawn\(\s*resolveGitBinary\(\)/)
  requireHiddenChildOptions(source, "execFileSync('taskkill'")
  requireHiddenChildOptions(source, /spawn\(\s*command,\s*args/)
  requireHiddenChildOptions(source, "spawn('curl'")
  requireHiddenChildOptions(source, /spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /hermesProcess = spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /spawn\(\s*py,\s*\['-m', 'hermes_cli\.main', 'uninstall', '--gui-summary'\]/)

  assert.match(source, /function unwrapWindowsVenvHermesCommand\(command, backendArgs\)/)
  assert.match(source, /getVenvSitePackagesEntries,/)
  assert.match(source, /function inferVenvRootForPython\(root, python\)/)
  assert.match(source, /args: \['-m', 'hermes_cli\.main', \.\.\.backendArgs\]/)
})

test('source backend PYTHONPATH follows the selected Python venv', () => {
  const source = readElectronFile('main.ts')
  const createIndex = source.indexOf('function createPythonBackend(')
  assert.notEqual(createIndex, -1, 'missing source backend resolver')
  const snippet = source.slice(createIndex, createIndex + 900)

  assert.match(snippet, /const command = IS_WINDOWS && fileExists\(legacyVenvPython\) \? legacyVenvPython : python/)
  assert.match(snippet, /const venvRoot = inferVenvRootForPython\(root, command\)/)
  assert.match(snippet, /pythonPathEntries: \[root, \.\.\.getVenvSitePackagesEntries\(venvRoot\)\]/)
  assert.doesNotMatch(snippet, /const venvRoot = path\.join\(root, 'venv'\)/)
})

test('desktop backend launches console python so child consoles are inherited, not pythonw', () => {
  const source = readElectronFile('main.ts')

  // The flash fix is structural: the backend runs as a console-subsystem
  // python.exe under hiddenWindowsChildOptions() (-> CREATE_NO_WINDOW), so it
  // owns ONE windowless console that every descendant spawn inherits. Launching
  // it as GUI-subsystem pythonw.exe is what made each child allocate (and flash)
  // its own console, so the backend command must never be pythonw.
  assert.doesNotMatch(source, /pythonw\.exe'\)/, 'backend must not be launched via pythonw.exe')
  assert.doesNotMatch(
    source,
    /function getNoConsoleVenvPython\b/,
    'pythonw-conversion helper should be gone; console python is launched directly'
  )
  assert.doesNotMatch(
    source,
    /function applyWindowsNoConsoleSpawnHints\b/,
    'pythonw spawn-hint rewriter should be gone'
  )

  // Console python restores stdout, so the port is announced on the normal
  // HERMES_DASHBOARD_READY stdout line — no ready-file side channel is set.
  assert.doesNotMatch(source, /readyFile: true/, 'no backend should opt into the pythonw ready-file path')

  // Both desktop backend launches must still go through hiddenWindowsChildOptions
  // so the single backend console is created windowless.
  requireHiddenChildOptions(source, /spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /hermesProcess = spawn\(\s*backend\.command,\s*backend\.args/)
})

test('desktop backend teardown tree-kills Windows backend descendants', () => {
  const source = readElectronFile('main.ts')

  const helperIndex = source.indexOf('function stopBackendChild(child)')
  assert.notEqual(helperIndex, -1, 'missing backend teardown helper')
  const helperSnippet = source.slice(helperIndex, helperIndex + 500)
  assert.match(helperSnippet, /stopBackendChildImpl\(child/)

  const resetIndex = source.indexOf('function resetHermesConnection(')
  assert.notEqual(resetIndex, -1, 'missing resetHermesConnection')
  const resetSnippet = source.slice(resetIndex, resetIndex + 300)
  assert.match(resetSnippet, /stopBackendChild\(hermesProcess\)/)
  assert.doesNotMatch(resetSnippet, /hermesProcess\.kill\('SIGTERM'\)/)

  const quitIndex = source.indexOf("app.on('before-quit'")
  assert.notEqual(quitIndex, -1, 'missing before-quit handler')
  const quitSnippet = source.slice(quitIndex, quitIndex + 900)
  assert.match(quitSnippet, /stopBackendChild\(backendConnectionState\.getProcess\(\)\)/)
  assert.doesNotMatch(quitSnippet, /hermesProcess\.kill\('SIGTERM'\)/)
})

test('pooled backend startup failures clean up partial children', () => {
  const source = readElectronFile('main.ts')
  const ensureIndex = source.indexOf('async function ensureBackend(profile)')
  assert.notEqual(ensureIndex, -1, 'missing ensureBackend')
  const snippet = source.slice(ensureIndex, ensureIndex + 1400)

  assert.match(snippet, /spawnPoolBackend\(key, entry\)\.catch\(error => \{/)
  assert.match(snippet, /event: 'startup\.failure'/)
  assert.match(snippet, /stopBackendChild\(entry\.process\)/)
  assert.match(snippet, /backendPool\.delete\(key\)/)
  assert.match(snippet, /throw error/)
})

test('pooled backends are never killed by a wall-clock idle timer', () => {
  const source = readElectronFile('main.ts')

  // A missed renderer lease during restart, reconnect, suspend, or scheduling
  // delay cannot prove that a backend is idle. Socket/profile pruning and the
  // bounded LRU path own cleanup without SIGTERMing an active turn.
  assert.doesNotMatch(source, /POOL_IDLE_MS/)
  assert.doesNotMatch(source, /startPoolIdleReaper/)
  assert.doesNotMatch(source, /Reaping idle profile backend/)
  assert.match(source, /evictLruPoolBackends/)
  assert.match(source, /if \(ready && !entry\.intentionalStop\) \{\s*sendBackendExit\(\{ code, signal, profile, pooled: true \}\)/)

  const stopIndex = source.indexOf('function stopPoolBackend(profile)')
  assert.notEqual(stopIndex, -1, 'missing pooled backend teardown')
  assert.match(source.slice(stopIndex, stopIndex + 300), /entry\.intentionalStop = true\s*stopBackendChild/)
})

test('intentional or interactive desktop child processes stay documented', () => {
  const source = readElectronFile('main.ts')

  assert.match(source, /windowsHide: false/)
  assert.match(source, /handOffWindowsBootstrapRecovery/)
  assert.match(source, /from '.\/windows-hermes-path'/)
  assert.match(source, /nodePty\.spawn\(command, args/)
  assert.match(source, /spawn\('cmd\.exe', \['\/c', 'start'/)
})

test('bootstrap PowerShell runner hides Windows console children', () => {
  const source = readElectronFile('bootstrap-runner.ts')

  assert.match(source, /import \{ hiddenWindowsChildOptions \} from '.\/windows-child-options'/)
  requireHiddenChildOptions(source, /spawn\(\s*ps,\s*fullArgs/)
})
