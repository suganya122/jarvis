#!/bin/sh
# Install the git pre-commit hook that refuses commits containing secrets (API keys, tokens).
cd "$(git rev-parse --show-toplevel)" || exit 1
cat > .git/hooks/pre-commit <<'HOOK'
#!/bin/sh
exec python3 scripts/check_secrets.py --staged
HOOK
chmod +x .git/hooks/pre-commit
echo "Installed: commits are checked for secrets first."
