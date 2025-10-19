import logging
import subprocess
from pathlib import Path

from .config import BirdNetConfig, CameraConfig


logger = logging.getLogger(__name__)


class AudioCapture:
    def __init__(
        self,
        camera_config: CameraConfig,
        birdnet_config: BirdNetConfig,
        output_file: str,
    ):
        self.camera_config = camera_config
        self.birdnet_config = birdnet_config
        self.output_file = Path(output_file)

    def capture(self) -> None:
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.camera_config.rtsp_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.birdnet_config.sample_rate),
            "-c:a",
            "pcm_s16le",
            "-t",
            str(self.camera_config.record_time),
            str(self.output_file),
        ]

        result = subprocess.run(cmd)  # noqa: PLW1510 - ffmpeg output is user-facing
        if result.returncode != 0:
            logger.warning(
                "ffmpeg exited with %s while capturing from %s to %s",
                result.returncode,
                self.camera_config.rtsp_url,
                self.output_file,
            )
