#!/usr/bin/env python3
"""Hyphenated CLI entrypoint for the narrow live Netcraze NDM adapter.

Live writes are deliberately bound to an explicitly acknowledged hardware
contract.  Status/plan remain usable without that acknowledgement.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from routerkit_netcraze_live import SUPPORTED_CONTRACT, main as core_main


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
