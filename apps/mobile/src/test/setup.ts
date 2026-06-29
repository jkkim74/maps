import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// 각 테스트 후 DOM·localStorage 정리.
afterEach(() => {
  cleanup()
  localStorage.clear()
})
