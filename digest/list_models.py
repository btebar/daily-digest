"""List Gemini models your API key can use for text generation.

    python -m digest.list_models

Set the model you want in config.yaml (curation.model).
"""

from __future__ import annotations

import os
import sys

from google import genai


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Set GEMINI_API_KEY (or GOOGLE_API_KEY) first.")
        return 1
    client = genai.Client(api_key=api_key)
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions or not actions:
            # m.name is like "models/gemini-2.5-flash"; the config wants the tail.
            print(m.name.split("/")[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
