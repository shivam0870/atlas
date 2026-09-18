# Shared implementation contracts

Lead owns this file, migrations, db.py, config.py, main.py, api.py and dependency manifests/locks. Agents request shared edits. No worker spawns other agents.

## Identity and transport

- Browser cookie: atlas_user_session (opaque token, server stores digest); integration clients: Authorization Bearer API key.
- Organization comes from X-Atlas-Tenant UUID, reflected by frontend /o/:tenantId/* URL. Never store a mutable active organization in the session.
- src/atlas/db.py exposes identity_transaction() for the dedicated identity role, transaction(tenant_id) for content, bind_identity(identity) and access_context ContextVar.
- Identity preserves old tenant_id,key_id,name,scopes positional fields and adds user_id,role,principal_kind ('user' or 'service'),principal_id,auth_revision,session_id.
- authenticate requires active tenant membership or service grants; account routes use a separate current_user dependency with no tenant required.
- JSON errors retain detail string; validation errors standard FastAPI until central error normalization. Every UI mutation sends X-Atlas-Client: console; cookie mutations checked against configured app origin.
- New API account prefix /api/auth; organization list/create /api/organizations; spaces /api/spaces; library /api/library. Existing tenant endpoints remain compatible where authorized.

## Schema sequence

- 006: users, user_sessions, account_tokens, recovery_codes, memberships, invitations, teams/team_members, service_accounts, tenant metadata, auth helpers and identity role.
- 007: spaces/resource_grants, immutable document_versions and chunk version IDs, private conversations/messages, bookmarks/feedback/notifications/access_requests/audit and admin support; resource RLS.
- Lead is sole schema/migration owner. No SQL migration execution by workers.
- Existing tenant IDs and content preserved. Administrative upgrade/seed actions use explicit privileged roles; missing principal in content requests fails closed.

## Knowledge

- spaces: tenant_id,id,name,description,visibility company|restricted,created_by,owner_user_id,tags.
- resource_grants: tenant_id,id,space_id OR document_id,subject_type user|team|service,subject_id,permission read|write.
- documents add space_id,owner_user_id,tags,review_due_at,lifecycle active|archived|trashed,restricted,current_version_id,pending_version_id.
- document_versions: tenant_id,id,document_id,number,title,content,content_hash,media_type,filename,storage_key,byte_size,source_segments JSON [{start,end,page?,section?}],status,created_at.
- chunks.version_id points to immutable version. Old chunks backfilled. Retrieval only current ready version; historic citation endpoints allow accessible historical ready versions.
- SQL atlas.can_space(id,write=false), can_document(id,write=false), can_entity(id,write=false). Owners/admins inspect company knowledge; other principals need space access; document restrictions only narrow it. Worker role is privileged only for scoped processing.
- Effective principal flows through all transactions via ContextVar. Policy changes bump tenant auth_revision. Cache namespace includes principal/revision.

## Frontend

- Frontend agent owns shell/shared components, auth/org feature modules and legacy extraction; lead owns web/src/features/WorkspaceRoutes.tsx and assigns library/chat/admin screen files explicitly later.
- Shared useWorkspace() => organization,user,tenantId,canManage,canEdit; api<T>(path,{tenantId,...RequestInit}); useScopedQueryKey(...parts).
- Shared UI Button,Field,PageHeader,EmptyState,ErrorNotice,Modal,Badge; precise module paths published by frontend owner.
- Lead installs React Router, TanStack Query, Radix primitives; worker requests additions without editing manifests.
- Local Mailpit browser inbox 127.0.0.1:58025, SMTP127.0.0.1:51025. No external mail sent.

## Worker ownership

identity: auth.py/accounts.py/organizations.py/mail.py + test_accounts/test_organizations.
knowledge: knowledge.py/extraction.py/ingestion.py/retrieval.py + test_knowledge/test_resource_access.
frontend: web/src app/components/styles/api/types/auth/organizations/main/style + new upgrade-auth browser tests.
lead: everything else until explicitly reassigned.
