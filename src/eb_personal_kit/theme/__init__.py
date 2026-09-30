"""Sunrise/sunset driven system colour-scheme switcher.

Computes the local sunrise and sunset times for a configured location
(offline, via ``astral``) and switches the OS colour scheme between light
(daytime) and dark (after dusk) on a fixed check cadence. Platform appliers
are resolved at runtime; ``astral`` is imported lazily so the main
``eb_personal_kit`` package stays dependency-free.
"""
