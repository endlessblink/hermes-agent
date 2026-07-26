export async function quitHermes() {
  const quit = window.hermesDesktop?.quit

  if (!quit) {
    throw new Error('Quit is unavailable in this Hermes build')
  }

  return quit()
}
