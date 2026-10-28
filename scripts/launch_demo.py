#!/usr/bin/env python3
"""Compatibility entrypoint; launches the working platform without demo data."""
if __package__:
    from .launch import main, stop
else:
    from launch import main, stop

if __name__ == '__main__':
    raise SystemExit(main())
