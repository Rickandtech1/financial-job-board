#!/usr/bin/env bash
# save_pipeline.sh — watches ~/Downloads for a fresh pipeline.json export,
# moves it to the project folder, and commits it to GitHub.

PROJECT="/Users/sarikeng/Projects/financial-job-board"
DOWNLOADS="$HOME/Downloads"
TARGET="$PROJECT/pipeline.json"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         Save Pipeline → GitHub                       ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "→ Opening the job board now..."
open "https://rickandtech1.github.io/financial-job-board/"
echo ""
echo "→ Once the page loads, click the  ⬇ Export Pipeline  button."
echo "  Waiting up to 60 seconds for pipeline.json to appear in ~/Downloads..."
echo ""

# Record a timestamp so we only pick up a freshly downloaded file
START_TIME=$(date +%s)

for i in $(seq 1 30); do
    CANDIDATE="$DOWNLOADS/pipeline.json"
    if [ -f "$CANDIDATE" ]; then
        # Only accept if the file was modified after this script started
        FILE_TIME=$(stat -f "%m" "$CANDIDATE" 2>/dev/null || echo 0)
        if [ "$FILE_TIME" -ge "$START_TIME" ]; then
            echo "✓ Detected fresh pipeline.json in ~/Downloads"
            mv "$CANDIDATE" "$TARGET"
            echo "✓ Moved to $TARGET"
            echo ""

            cd "$PROJECT" || { echo "ERROR: cannot cd to $PROJECT"; exit 1; }
            git add pipeline.json
            git commit -m "Save pipeline $(date +%Y-%m-%d)"
            git push origin main

            echo ""
            echo "✓ Pipeline saved and pushed to GitHub."
            exit 0
        fi
    fi
    sleep 2
done

echo ""
echo "✗ Timed out — no fresh pipeline.json found in ~/Downloads after 60 seconds."
echo "  Make sure you clicked ⬇ Export Pipeline on the job board."
exit 1
