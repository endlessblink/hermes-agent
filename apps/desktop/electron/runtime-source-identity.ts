import { execFileSync } from 'node:child_process'
import path from 'node:path'

type ExecFileSyncLike = (
  command: string,
  args: readonly string[],
  options: Record<string, unknown>
) => string | Buffer

export function runtimeSourceRootCandidates(executablePath: string, maxDepth = 8): string[] {
  const candidates: string[] = []
  let current = path.resolve(path.dirname(executablePath))

  for (let depth = 0; depth <= maxDepth; depth += 1) {
    if (!candidates.includes(current)) {
      candidates.push(current)
    }
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }

  return candidates
}

export function resolveRuntimeSourceIdentity({
  command,
  root,
  env,
  execFileSyncImpl = execFileSync as ExecFileSyncLike
}: {
  command: string
  root: string
  env: Record<string, string>
  execFileSyncImpl?: ExecFileSyncLike
}): Record<string, string> | null {
  const resolvedRoot = path.resolve(root)

  try {
    const raw = execFileSyncImpl(command, ['-m', 'hermes_cli.runtime_source', '--root', resolvedRoot], {
      cwd: resolvedRoot,
      encoding: 'utf8',
      env: { ...process.env, ...env },
      maxBuffer: 1024 * 1024,
      timeout: 60_000,
      windowsHide: true
    })
    const value = JSON.parse(String(raw))
    const revision = typeof value?.revision === 'string' ? value.revision : ''
    const digest = typeof value?.sourceManifestDigest === 'string' ? value.sourceManifestDigest : ''
    const outputRoot = typeof value?.root === 'string' ? path.resolve(value.root) : ''

    if (
      value?.ok !== true ||
      value?.schemaVersion !== 1 ||
      outputRoot !== resolvedRoot ||
      !/^[0-9a-f]{40,64}$/.test(revision) ||
      !/^[0-9a-f]{64}$/.test(digest)
    ) {
      return null
    }

    return {
      HERMES_RUNTIME_BUILD_ID: `source-${revision.slice(0, 12)}-${digest.slice(0, 12)}`,
      HERMES_RUNTIME_SOURCE_MANIFEST_DIGEST: digest,
      HERMES_RUNTIME_SOURCE_ROOT: resolvedRoot
    }
  } catch {
    return null
  }
}
