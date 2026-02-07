# Backend Feature Flags (v1)

## v1 Position
- **No dedicated backend feature-flag framework is implemented for v1**.
- v1 boundaries are enforced by endpoint surface + sync allowlists, not runtime flags.

## Effective Gates in Current Code
- Sync push entity allowlist in `hisabi_backend/api/v1/sync.py` (`SYNC_PUSH_ALLOWLIST`).
- Global bearer-auth enforcement on v1 endpoints in `utils/bearer_auth.py` + `hooks.py`.
- Wallet role ACL checks in `utils/wallet_acl.py`.

## v1 Boundary Flags (Conceptual, Not Implemented)
- `enableCollaboration=false`
- `enableSharedWallets=false`
- `enableRemoteTransfers=false`
- `enableFamilyGroups=false`
- `enableSplitBills=false`
- `enableSubscriptions=false`

If a formal flag system is introduced later, map these booleans to route-level and handler-level gates before enabling non-v1 features.
