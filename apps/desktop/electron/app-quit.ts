export function createQuitMenuItem(quit: () => void) {
  return {
    label: 'Quit Hermes',
    accelerator: 'CommandOrControl+Q',
    click: quit
  }
}
