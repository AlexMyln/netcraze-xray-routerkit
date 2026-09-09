#!/usr/bin/env python3
"""Hyphenated CLI entrypoint for the narrow live Netcraze NDM adapter.

Live writes are deliberately bound to an explicitly acknowledged hardware
contract. Status/plan remain usable without that acknowledgement. The public
entrypoint also enables strict semantic reuse of native objects created during
the first NC-3812 hardware installation, whose human-readable descriptions
predate RouterKit's code-owned naming convention.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

import routerkit_netcraze_live as core
from routerkit_netcraze_live_compat import build_live_plan, verify_plan_applied


# The public CLI is the supported live execution boundary. Inject only the
# hardware-proven semantic-reuse planner/verifier; mutation/rendering remains
# in the reviewed core adapter.
core.build_live_plan = build_live_plan
core._verify_plan_applied = verify_plan_applied
SUPPORTED_CONTRACT = core.SUPPORTED_CONTRACT
core_main = core.main


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "apply":
        try:
            index = args.index("--contract")
            contract = args[index + 1]
        except (ValueError, IndexError):
            print(
                "routerkit-netcraze-live: apply requires explicit --contract %s"
                % SUPPORTED_CONTRACT,
                file=sys.stderr,
            )
            return 2
        if contract != SUPPORTED_CONTRACT:
            print(
                "routerkit-netcraze-live: unsupported live hardware contract",
                file=sys.stderr,
            )
            return 2
    return core_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
