"""Generate local configuration once. Never overwrite existing operator secrets."""

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

target = Path(".env")
descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(descriptor, "w") as stream:
    stream.write(f"CLOUDER_ENCRYPTION_KEY={Fernet.generate_key().decode()}\n")
    stream.write(f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n")
    stream.write("CLOUDER_PROVIDER=simulated\n")
print("Created .env with fresh credentials. Back up the encryption key securely.")
