// Ignore locations so moving code does not create a new error. Count each
// fingerprint so an additional identical error cannot reuse one allowance.
export function newDiagnostics(current, baseline) {
  const remaining = new Map()
  for (const diagnostic of baseline) {
    const key = JSON.stringify(diagnostic)
    remaining.set(key, (remaining.get(key) ?? 0) + 1)
  }
  return current.filter(diagnostic => {
    const key = JSON.stringify(diagnostic)
    const count = remaining.get(key) ?? 0
    if (!count) return true
    remaining.set(key, count - 1)
    return false
  })
}
