"""Deploy the demo to a Hugging Face Space (Docker SDK).

    hf auth login --token hf_xxx          # once, a write token
    python scripts/deploy_space.py <user-or-org>/<space-name> [--private]

Stages the repository (minus ignored and generated files) into a temp folder with a
Space-specific README front matter, creates the Space if needed, and uploads the folder.
The Space then builds the Dockerfile: pipeline + model download happen at build time,
so the app answers immediately once the build finishes (about 10 minutes the first time).
"""

import argparse
import fnmatch
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent

SPACE_README = """---
title: EHR Media Intelligence
emoji: 🩺
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Semantic search and AI summaries over synthetic FHIR records
---

# EHR Media Intelligence Platform (demo)

Synthetic EHR records (Faker, fixed seed; no real patient data) cleaned, mapped to HL7 FHIR
R4, summarised by an LLM with grounding checks, and searchable by meaning. The UI is served
from the same container as the API.

- Search as you type; filter by record type and date; open a patient for the AI summary and
  the records it cites.
- `GET /health`, `POST /search`, `GET /patients/{id}`; interactive docs at `/docs`.
- Summaries come from the committed cache (Claude Haiku, prompt v2); the container itself
  runs the mock provider, so no API key is involved.

Source and write-up: https://github.com/Miki-b/EHR-Media-Intelligence-Platform
"""


def ignored_patterns() -> list[str]:
    patterns = []
    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("!"):
            patterns.append(line.rstrip("/"))
    return patterns


def should_skip(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
        if any(fnmatch.fnmatch("/".join(parts[: i + 1]), pat) for i in range(len(parts))):
            return True
    return False


def stage(dest: Path) -> int:
    patterns = ignored_patterns()
    count = 0
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if should_skip(rel, patterns) or rel == "README.md":
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    (dest / "README.md").write_text(SPACE_README, encoding="utf-8")
    return count + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_id", help="e.g. my-user/ehr-media-intelligence")
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()

    api = HfApi()
    who = api.whoami()["name"]
    print(f"logged in as {who}")
    url = api.create_repo(
        args.repo_id, repo_type="space", space_sdk="docker", private=args.private, exist_ok=True
    )
    print(f"space: {url}")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "space"
        n = stage(dest)
        print(f"staged {n} files, uploading…")
        api.upload_folder(
            repo_id=args.repo_id,
            repo_type="space",
            folder_path=str(dest),
            commit_message="Deploy EHR Media Intelligence demo",
            delete_patterns=["*"],
        )
    print(f"uploaded. Build logs: {url}?logs=build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
