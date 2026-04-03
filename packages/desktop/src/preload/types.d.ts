import type { CeridBridge } from './preload'

declare global {
  interface Window {
    cerid: CeridBridge
  }
}

export {}
