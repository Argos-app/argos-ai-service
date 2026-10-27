#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
    main_api|mcp_data|mcp_email|mcp_prediction|a2a_agent) service="$1" ;;
    *) echo 'Expected one of the five application directory names.' >&2; exit 1 ;;
esac

cd "$(dirname "$0")/../.."
python -m pip install --disable-pip-version-check pip==26.2.1
python -m pip install --disable-pip-version-check -r "apps/$service/pylock.toml"
