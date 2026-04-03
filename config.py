# Configuration File
# ------------------
# Define constants and default values here.

import struct

# Network
DEFAULT_IP = "0.0.0.0"
DEFAULT_PORT = 10000

# Data Protocol
IMG_WIDTH = 128
IMG_HEIGHT = 128
PIXEL_COUNT = IMG_WIDTH * IMG_HEIGHT


BYTES_PER_PIXEL = 4
FRAME_PAYLOAD_SIZE = PIXEL_COUNT * BYTES_PER_PIXEL


# Protocol Constants (Confirmed)
PACKET_HEADER = b'\x55\xaa'  # Little-endian or Big-endian? 0xAA55 usually means 0x55 then 0xAA if little-endian. Let's assume network order.
# User said "1~2 帧头 START 0XAA55".
# Let's assume standard network byte order (Big Endian) for now unless specified.
# struct.unpack('>H') -> 0xAA55. So bytes would be \xaa\x55.

# Packet Structure Offsets (0-indexed)
OFFSET_START = 0
OFFSET_CONTROL = 2
OFFSET_TASK_ID = 4
OFFSET_TYPE = 7
OFFSET_SEQ = 8
OFFSET_DATA = 9
DATA_LEN = 4096
OFFSET_FCS = 9 + DATA_LEN
OFFSET_END = 9 + DATA_LEN + 1

PACKET_SIZE = OFFSET_END + 2 # 4108 bytes

# Task Types
TASK_TYPE_INTENSITY_RANGE = 0
TASK_TYPE_TOF = 1

# Debug
RECEIVER_DEBUG = False

# Fragment Count
TOTAL_FRAGMENTS = 16 # 65536 / 4096
TOF_TOTAL_FRAGMENTS = 16

# Scaling Factors
RANGE_SCALE_FACTOR = 10.0  # Real Value = Raw / 10.0
INTENSITY_MAX_VAL = 200    # Expected max intensity value

# UI Defaults
DEFAULT_INTENSITY_CMAP = None
DEFAULT_RANGE_CMAP = 'jet'
DEFAULT_TOF_CMAP = 'plasma'
