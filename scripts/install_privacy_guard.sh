#!/bin/sh
# Install the pre-push privacy check for this clone. Safe to run again.
set -e
hook="$(git rev-parse --git-path hooks)/pre-push"
cat > "$hook" <<'HOOK'
#!/bin/sh
# Stops a push that would publish personal information. See scripts/check_personal_info.py.
exec python3 "$(git rev-parse --show-toplevel)/scripts/check_personal_info.py" "$@"
HOOK
chmod +x "$hook"
echo "Privacy check installed. Add values to protect with:"
echo "  python3 scripts/check_personal_info.py --add"
