# Default configuration

# Video
DEFAULT_CAMERA_INDEX = 0
DEFAULT_FPS = 20
DEFAULT_RESOLUTION = (1280, 720)

# Video effects defaults
DEFAULT_BRIGHTNESS = 0      # -100 to 100
DEFAULT_CONTRAST = 0        # -100 to 100
DEFAULT_SATURATION = 0      # -100 to 100
DEFAULT_GAMMA = 1.0         # 0.1 to 3.0

# Audio
DEFAULT_AUDIO_CHANNELS = 2
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_AUDIO_BLOCKSIZE = 1024

# Audio effects defaults
DEFAULT_PITCH = 1.0         # 0.5 to 2.0
DEFAULT_GAIN = 1.0          # 0.0 to 3.0
DEFAULT_REVERB = 0.0        # 0.0 to 1.0

# Virtual devices
VIRTUAL_CAM_BACKEND = "obs"
VIRTUAL_AUDIO_DEVICE = "CABLE Input"  # VB-CABLE output device name
