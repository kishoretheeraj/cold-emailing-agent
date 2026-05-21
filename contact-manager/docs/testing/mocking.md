# Mocking conventions (Vitest)

**Supabase chain mock** — the new query builder calls methods in a specific order and
`.limit()` must be the terminal resolver. Use a shared `readChain` object:
```ts
const { limitMock, updateEqMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  updateEqMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  const readChain: Record<string, unknown> = {};
  for (const m of ["is", "order", "or", "in", "eq", "lt"]) {
    readChain[m] = vi.fn(() => readChain);
  }
  readChain.limit = limitMock;
  return {
    supabase: {
      from: vi.fn(() => ({
        select: vi.fn(() => readChain),
        update: vi.fn(() => ({ eq: updateEqMock })),
      })),
    },
  };
});
```

**IntersectionObserver mock** — must be a plain function (not `vi.fn()`). `vi.restoreAllMocks()`
in afterEach will reset a `vi.fn()` implementation, breaking tests that capture the IO
callback. Install a plain function in `vitest.setup.ts` and override with another plain
function per-test if you need to capture the callback:
```ts
// vitest.setup.ts — base stub
global.IntersectionObserver = function() {
  return { observe() {}, disconnect() {}, unobserve() {} };
} as unknown as typeof IntersectionObserver;

// In individual test file — capturing version
let ioCallback: (...) => void;
global.IntersectionObserver = function(cb) {
  ioCallback = cb;
  return { observe() {}, disconnect() {}, unobserve() {} };
} as unknown as typeof IntersectionObserver;
```

**Radix / Vaul mocks** — these use portals. In tests that render components using Sheet
or ConfirmModal, mock the primitives so they render into the jsdom body without portal
quirks. See `ContactsList.test.tsx` for complete Vaul + Radix Dialog + Radix Select mock
examples.

**Sonner mock:**
```ts
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
```

- Variables used inside `vi.mock()` factories must be hoisted with `vi.hoisted()`.
- Reset mocks in `beforeEach`, not afterEach.
- **App shell test** (`App.test.tsx`): always assert that persistent nav links (e.g.
  "Overview", "Prompts", "Activity") exist with the correct `href`. When rewriting App.tsx,
  verify this test still passes before committing.
