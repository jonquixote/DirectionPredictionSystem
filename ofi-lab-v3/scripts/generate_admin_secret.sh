#!/usr/bin/env bash
# Generate a secure admin secret for V3_ADMIN_SECRET
python -c "import secrets; print(secrets.token_hex(32))"
