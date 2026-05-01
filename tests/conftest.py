import os

# Run Qt tests headless when no display is available.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
