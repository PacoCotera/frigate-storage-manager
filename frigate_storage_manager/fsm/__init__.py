"""Frigate Storage Manager. No maintenance is enabled in the 0.1.2 release."""

VERSION = "0.1.2"
# Deliberately not an environment variable, Supervisor option, or browser setting.
# Enabling requires a reviewed release after the live validation in issue #1.
DESTRUCTIVE_ENABLED = False
