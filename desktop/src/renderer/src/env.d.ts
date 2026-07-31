/// <reference types="vite/client" />
import type { AncestryBridge } from '../../shared-contract/desktop'
declare global { interface Window { ancestry: AncestryBridge } }
export {}
