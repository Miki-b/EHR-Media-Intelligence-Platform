"""One entry point for every pipeline stage.

    python -m app.pipeline ingest|fhir|summarize|index|all

Each stage is implemented in its own package and registered here as it lands.
"""

import argparse
import importlib
import logging
import sys

log = logging.getLogger("pipeline")

# stage name -> "module:function". Owners fill these in on their task branches.
STAGES: dict[str, str] = {
    "ingest": "app.ingest.run:main",  # Dev A, Task 1
    "fhir": "app.fhir.run:main",  # Dev A, Task 2
    "summarize": "app.summarize.run:main",  # Dev A, Task 3
    "index": "app.search.index:main",  # Dev B, Task 4
}


def run_stage(name: str) -> None:
    module_name, func_name = STAGES[name].split(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        log.warning("stage %-9s not implemented yet (%s)", name, module_name)
        return
    log.info("stage %-9s start", name)
    getattr(module, func_name)()
    log.info("stage %-9s done", name)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=[*STAGES, "all"])
    args = parser.parse_args(argv)
    for name in STAGES if args.stage == "all" else [args.stage]:
        run_stage(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
