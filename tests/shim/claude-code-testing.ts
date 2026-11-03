// `claude plugin test` provides the 'claude-code/testing' module. These tests only
// use describe, test and expect, so under plain Node (npm test) the same file runs
// on Vitest through this alias. vitest.config.ts points the import here.
export { describe, test, expect } from 'vitest'
