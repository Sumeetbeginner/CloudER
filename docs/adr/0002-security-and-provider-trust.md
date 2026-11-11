# ADR 0002: Explicit provider trust and a restricted initial catalog

Status: accepted for the engineering preview.

Tenants can provision approved service templates. They cannot specify arbitrary container images, entrypoints, Linux capabilities, host mounts, network modes, or raw upstream URLs. This narrows the attack surface and makes integration tests meaningful.

The API uses expiring random bearer keys hashed at rest, with owner/operator/viewer RBAC. Tenant identity comes only from the authenticated key. OIDC login, MFA, custom policies, group federation, and short-lived runtime identities are later milestones, not implicit capabilities of the term IAM.

Only workers receive Docker socket access. Rootless Docker on a dedicated development VM is preferred when supported and tested with resource limits; the supplied socket override is explicitly trusted-host development configuration. Docker is not offered as hostile multi-tenant isolation. Production workloads require an isolation design and escape testing appropriate to the threat model.

Fernet protects stored secret ciphertext against disclosure and tampering when the database is read without the key. It does not protect against a compromised process or trusted operator. Introduce external KMS/Vault, envelope encryption, rotation, and recovery drills before handling production secrets.
