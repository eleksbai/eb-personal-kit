"""Standalone system metrics monitor client.

Samples CPU, memory, disk, network and temperature metrics, encrypts them
with AES-256-GCM (key wrapped with the server RSA public key via RSA-OAEP)
and uploads them to the monitor server. Optional dependencies (``httpx``,
``psutil`` and ``cryptography``) are imported lazily so the main ``eb_tools``
package stays dependency-free.
"""
