// Web Storage shim for the vitest environment. Loaded before every test file
// via `test.setupFiles` in vitest.config.ts.
//
// Why this exists: Node 22+ ships its own `localStorage` / `sessionStorage`
// globals (Web Storage API; unflagged in Node 26). Without
// `--localstorage-file` the `localStorage` getter warns and yields undefined
// (Node 26) or throws ERR_INVALID_ARG_VALUE (Node 24 with
// --experimental-webstorage). vitest's jsdom environment only fills in
// globals that don't already exist, so Node's broken global wins over
// jsdom's Storage and every test that touches storage crashes on
// `localStorage.clear()`. Under Node <= 24 without the flag, jsdom's storage
// was used and everything passed — which is why CI (Node 22) never saw it.
//
// Fix: make sure both globals are real, working Storage objects — jsdom's
// when it is reachable through `window`, otherwise an in-memory one.

type AnyGlobal = Record<string, unknown>

function usable(candidate: unknown): candidate is Storage {
  return !!candidate
    && typeof (candidate as Storage).clear === 'function'
    && typeof (candidate as Storage).getItem === 'function'
}

function readProperty(target: object, name: string): unknown {
  try {
    return (target as AnyGlobal)[name]
  } catch {
    return undefined // Node's getter throws when no --localstorage-file is set
  }
}

function memoryStorage(): Storage {
  const data = new Map<string, string>()
  const storage = {} as Storage
  // Methods are non-enumerable so Object.keys(storage) lists only stored
  // keys, matching the real API (tests assert on that).
  Object.defineProperties(storage, {
    length: { get: () => data.size },
    key: { value: (index: number) => Array.from(data.keys())[index] ?? null },
    getItem: { value: (key: string) => data.get(String(key)) ?? null },
    setItem: { value: (key: string, value: unknown) => { data.set(String(key), String(value)) } },
    removeItem: { value: (key: string) => { data.delete(String(key)) } },
    clear: { value: () => { data.clear() } },
  })
  return storage
}

function install(name: 'localStorage' | 'sessionStorage'): void {
  // Preference: jsdom's Storage (reachable via window) > a usable global
  // (Node 26's native sessionStorage) > in-memory. Whichever wins is put on
  // BOTH globalThis and window so `sessionStorage === window.sessionStorage`
  // holds on every Node version — no early return, always converge.
  const win = readProperty(globalThis, 'window') as object | undefined
  const fromWindow = win && win !== globalThis ? readProperty(win, name) : undefined
  const fromGlobal = readProperty(globalThis, name)
  const replacement = usable(fromWindow) ? fromWindow
    : usable(fromGlobal) ? fromGlobal
    : memoryStorage()
  const targets = new Set<object>([globalThis])
  if (win) targets.add(win)
  for (const target of targets) {
    if (readProperty(target, name) === replacement) continue
    try {
      Object.defineProperty(target, name, {
        value: replacement, configurable: true, writable: true, enumerable: true,
      })
    } catch {
      (target as AnyGlobal)[name] = replacement
    }
  }
}

install('localStorage')
install('sessionStorage')
