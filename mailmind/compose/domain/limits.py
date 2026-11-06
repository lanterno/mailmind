"""How much a message may carry."""
MAX_ATTACH_BYTES = 25 << 20               # 25 MB total decoded attachment budget
# /api/send body cap must hold MAX_ATTACH_BYTES AFTER base64 (~4/3) + JSON overhead.
RESUME_HEADROOM = 64 << 10                 # the rest of a save: chips, subject, keys
MAX_SEND_POST = 38 << 20                  # 38 MB > 25 MB*4/3 (≈33.5 MB) + headroom
