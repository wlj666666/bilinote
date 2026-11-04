import assert from 'node:assert/strict'
import test from 'node:test'
import { newDiagnostics } from './ci-baseline.mjs'

const existing = { file: 'src/example.ts', code: 'TS2322', message: 'Wrong type', source: 'value' }

test('existing diagnostics pass and fixing an error does not fail CI', () => {
  assert.deepEqual(newDiagnostics([existing], [existing]), [])
  assert.deepEqual(newDiagnostics([], [existing]), [])
})

test('an additional occurrence of an existing diagnostic fails', () => {
  assert.deepEqual(newDiagnostics([existing, existing], [existing]), [existing])
})

test('replacing an existing error with a different error or file fails', () => {
  for (const changed of [
    { ...existing, file: 'src/new.ts' },
    { ...existing, code: 'TS2345' },
    { ...existing, message: 'Another error' },
    { ...existing, source: 'anotherValue' },
  ]) {
    assert.deepEqual(newDiagnostics([changed], [existing]), [changed])
  }
})
