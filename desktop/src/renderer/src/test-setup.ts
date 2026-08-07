/** Configures shared renderer test setup with jest-dom assertions and automatic cleanup. */
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)
