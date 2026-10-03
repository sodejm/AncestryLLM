/** Declares Vite types and the isolated renderer's versioned ancestry bridge. */
/// <reference types="vite/client" />
import type { AncestryBridge } from '../../shared-contract/desktop'
declare global { interface Window { ancestry: AncestryBridge } }
export {}
