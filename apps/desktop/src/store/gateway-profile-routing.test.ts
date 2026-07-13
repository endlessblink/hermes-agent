import { afterEach, describe, expect, it } from 'vitest'

import type { HermesGateway } from '@/hermes'

import { $gateway, gatewayForProfile, setPrimaryGateway } from './gateway'

describe('gatewayForProfile', () => {
  afterEach(() => {
    setPrimaryGateway(null, 'default')
    $gateway.set(null)
  })

  it('returns a profile gateway without replacing the foreground gateway', async () => {
    const foregroundGateway = { id: 'content-creator' } as unknown as HermesGateway
    const assistantOwnerGateway = { id: 'office-work' } as unknown as HermesGateway

    $gateway.set(foregroundGateway)
    setPrimaryGateway(assistantOwnerGateway, 'office-work')

    await expect(gatewayForProfile('office-work')).resolves.toBe(assistantOwnerGateway)
    expect($gateway.get()).toBe(foregroundGateway)
  })
})
