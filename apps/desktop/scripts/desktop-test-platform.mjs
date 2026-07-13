const SUPPORTED_BUNDLE_PLATFORMS = new Set(['darwin', 'linux', 'win32'])

export function supportsDesktopBundleValidation(platform) {
  return SUPPORTED_BUNDLE_PLATFORMS.has(platform)
}

export function desktopExecutableName(platform) {
  return platform === 'win32' ? 'Hermes.exe' : 'Hermes'
}
