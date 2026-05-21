# ThreadView component

`ThreadView.tsx` renders inside the Vaul side sheet in `ContactsList.tsx`. It fetches `email_messages` for the selected contact on mount. Outgoing messages are right-aligned indigo; incoming are left-aligned muted. Bodies >300 chars are truncated with an expand toggle.

**Testing**: `ContactsList.test.tsx` mocks `ThreadView` to `null` to avoid triggering the `email_messages` Supabase query inside the test's mock chain:
```ts
vi.mock("@/components/ThreadView", () => ({ ThreadView: () => null }));
```
The `ThreadView.test.tsx` file provides its own isolated mock of the supabase chain.
