"""Basic OS tools worker: files, commands, opening URLs and files, system info.

Runs as its own process (`python -m workers.os_tools`) so a crash here never takes down
core. Imports only from `contracts`.
"""
