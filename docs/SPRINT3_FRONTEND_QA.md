# Sprint 3 Frontend QA Checklist

## Offline-only flows
- [ ] Open app while offline and verify budgets/goals/debts/jameya pages display local data without errors.
- [ ] Ensure "عرض محفوظ" banners show when cached data is used and the UI remains responsive.
- [ ] Create/edit budgets, goals, debts, jameya items offline; confirm Dexie updates and no requests are sent.
- [ ] Local unlock and queue-based sync still work after offline mutations; no race when reconnecting.

## Linked + online flows
- [ ] After linking and going online, home dashboard fetches `report_summary` and shows branded KPIs, budget warnings, goal progress, debt net, and next jameya due.
- [ ] Budgets/Goals pages default to the cloud tab, refresh button re-fetches and updates cards, fallback cache works when offline.
- [ ] Debts page can send/accept/decline network requests; each action triggers a sync_pull to refresh Dexie.
- [ ] Jameya due payment action calls `mark_payment_paid` and syncs; Settings > Cloud Sync > Advanced rebuild button calls `rebuild_schedule` + sync.

## Auth / error cases
- [ ] When the device token is revoked or expired, every finance report or debt/jameya action shows the unauthorized message and prevents duplicate requests.
- [ ] Network errors map to Arabic friendly toasts (offline/not linked/server error) and do not crash the app.

## Performance / data scale
- [ ] Report data is cached (`reportCache`) and reused immediately when offline, preventing extra syncs.
- [ ] Heavy chart recalculations are avoided on the critical path (reports throttle to once per reconnect/reload).
- [ ] Home page remains responsive with thousands of transactions while server report data is asynchronous.
