import logging
import subprocess
from pathlib import Path
from typing import List

from .config import BirdNetConfig, StreamConfig


logger = logging.getLogger(__name__)


class AudioCapture:
    def __init__(
        self,
        stream_config: StreamConfig,
        birdnet_config: BirdNetConfig,
        output_file: str,
    ):
        self.stream_config = stream_config
        self.birdnet_config = birdnet_config
        self.output_file = Path(output_file)

    def _build_command(self) -> List[str]:
        cmd: List[str] = ["ffmpeg"]
        kind = self.stream_config.kind
        url = self.stream_config.url

        if kind == "rtsp":
            cmd.extend(["-rtsp_transport", "tcp", "-i", url])
        elif kind in {"http", "https", "file"}:
            cmd.extend(["-i", url])
        else:
            raise ValueError(f"Unsupported stream kind '{kind}' for ffmpeg capture")

        cmd.extend(
            [
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(self.birdnet_config.sample_rate),
                "-c:a",
                "pcm_s16le",
                "-t",
                str(self.stream_config.record_time),
                str(self.output_file),
            ]
        )
        return cmd

    def capture(self) -> None:
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command()

        result = subprocess.run(cmd)  # noqa: PLW1510 - ffmpeg output is user-facing
        if result.returncode != 0:
            logger.warning(
                "ffmpeg exited with %s while capturing from %s to %s",
                result.returncode,
                self.stream_config.url,
                self.output_file,
            )
