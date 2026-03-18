#!/bin/bash
set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PORT=2555
CONFIG="$DIR/litellm_config.yaml"

echo ""
echo "  Claude Code Alias Setup"
echo "  ========================"
echo ""
echo "  This creates a shell alias so you can run Claude Code"
echo "  backed by a model from your LiteLLM proxy."
echo ""

# --- Detect shell profile ---

SHELL_NAME="$(basename "$SHELL")"
case "$SHELL_NAME" in
    zsh)  PROFILE="$HOME/.zshrc" ;;
    bash) PROFILE="$HOME/.bashrc" ;;
    *)    PROFILE="$HOME/.${SHELL_NAME}rc" ;;
esac

echo "  Detected shell: $SHELL_NAME ($PROFILE)"
echo ""

# --- Read available models from config ---

if [ ! -f "$CONFIG" ]; then
    echo "  ✗ Config not found at $CONFIG"
    echo "    Run ./litellm.sh up first."
    exit 1
fi

MODELS=($(grep "model_name:" "$CONFIG" | sed 's/.*model_name: *//'))

if [ ${#MODELS[@]} -eq 0 ]; then
    echo "  ✗ No models configured."
    echo "    Run ./litellm.sh add first."
    exit 1
fi

echo "  Available models:"
echo ""
for i in "${!MODELS[@]}"; do
    echo "    [$((i+1))] ${MODELS[$i]}"
done
echo ""

read -p "  Choose model [1]: " MODEL_CHOICE
MODEL_CHOICE="${MODEL_CHOICE:-1}"
MODEL_IDX=$((MODEL_CHOICE - 1))

if [ "$MODEL_IDX" -lt 0 ] || [ "$MODEL_IDX" -ge "${#MODELS[@]}" ]; then
    echo "  ✗ Invalid choice."
    exit 1
fi

MODEL="${MODELS[$MODEL_IDX]}"
echo "  Selected: $MODEL"
echo ""

# --- Choose alias name ---

DEFAULT_ALIAS="claude-${MODEL}"
read -p "  Alias name [$DEFAULT_ALIAS]: " ALIAS_NAME
ALIAS_NAME="${ALIAS_NAME:-$DEFAULT_ALIAS}"

# Validate alias name (alphanumeric, hyphens, underscores)
if [[ ! "$ALIAS_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "  ✗ Invalid alias name. Use letters, numbers, hyphens, underscores."
    exit 1
fi

echo ""

# --- Read master key from .env ---

MASTER_KEY="sk-1234"
if [ -f "$DIR/.env" ]; then
    KEY=$(grep "^LITELLM_MASTER_KEY=" "$DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
    if [ -n "$KEY" ]; then
        MASTER_KEY="$KEY"
    fi
fi

# --- Build the alias ---

ALIAS_LINE="alias ${ALIAS_NAME}='ANTHROPIC_BASE_URL=\"http://localhost:${PORT}\" ANTHROPIC_MODEL=\"${MODEL}\" ANTHROPIC_API_KEY=\"${MASTER_KEY}\" CLAUDE_CODE_DISABLE_1M_CONTEXT=1 claude'"
COMMENT="# Claude Code via LiteLLM Proxy (${MODEL})"

# --- Check for existing alias ---

if grep -q "alias ${ALIAS_NAME}=" "$PROFILE" 2>/dev/null; then
    echo "  ⚠ Alias '${ALIAS_NAME}' already exists in $PROFILE."
    read -p "  Overwrite? [y/N]: " OVERWRITE
    if [[ "${OVERWRITE,,}" != "y" ]]; then
        echo "  Cancelled."
        exit 0
    fi
    # Remove old alias line and its comment
    sed -i.bak "/# Claude Code via LiteLLM Proxy.*${ALIAS_NAME}\|alias ${ALIAS_NAME}=/d" "$PROFILE"
    echo "  Removed old alias."
fi

# --- Write to profile ---

echo "" >> "$PROFILE"
echo "$COMMENT" >> "$PROFILE"
echo "$ALIAS_LINE" >> "$PROFILE"

echo "  ✓ Added to $PROFILE:"
echo ""
echo "    $ALIAS_LINE"
echo ""

# --- Source it ---

echo "  To activate now, run:"
echo ""
echo "    source $PROFILE"
echo ""
echo "  Then use it:"
echo ""
echo "    $ALIAS_NAME"
echo ""
echo "  Make sure the proxy is running first:"
echo ""
echo "    cd $DIR && ./litellm.sh up && ./litellm.sh login openai"
echo ""
