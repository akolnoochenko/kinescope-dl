import sys
from io import BytesIO
from os import PathLike
from typing import Union
from pathlib import Path
from requests import Session
from subprocess import Popen
from shutil import copyfileobj, rmtree, move
from base64 import b64decode, b64encode
from requests.exceptions import ChunkedEncodingError

from tqdm import tqdm
from mpegdash.parser import MPEGDASHParser, MPEGDASH

from kinescope.kinescope import KinescopeVideo
from kinescope.const import KINESCOPE_BASE_URL
from kinescope.exceptions import *


class VideoDownloader:
    def __init__(self, kinescope_video: KinescopeVideo,
                 temp_dir: Union[str, PathLike] = './temp',
                 ffmpeg_path: Union[str, PathLike] = './ffmpeg',
                 mp4decrypt_path: Union[str, PathLike] = './mp4decrypt',
                 audio_only=False):
        self.kinescope_video: KinescopeVideo = kinescope_video

        self.temp_path: Path = Path(temp_dir)
        self.temp_path.mkdir(parents=True, exist_ok=True)

        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            meipass_path = Path(sys._MEIPASS).resolve()
            self.ffmpeg_path = meipass_path / 'ffmpeg'
            self.mp4decrypt_path = meipass_path / 'mp4decrypt'
        else:
            self.ffmpeg_path = ffmpeg_path
            self.mp4decrypt_path = mp4decrypt_path

        self.http = Session()

        self.mpd_master: MPEGDASH = self._fetch_mpd_master()

        self.audio_only = audio_only

    def __del__(self):
        rmtree(self.temp_path)

    def _merge_tracks(self, source_filepaths: list[str | PathLike],
                      target_filepath: str | PathLike):
        args = [self.ffmpeg_path]
        for p in source_filepaths:
            args += '-i', p
        args += '-c', 'copy', target_filepath
        args += '-y', '-loglevel', 'error'
        try:
            Popen(args).communicate()
        except FileNotFoundError:
            raise FFmpegNotFoundError('FFmpeg binary was not found at the specified path')

    def _decrypt_video(self, source_filepath: str | PathLike,
                       target_filepath: str | PathLike,
                       key: str):
        try:
            Popen((self.mp4decrypt_path,
                   "--key", f"1:{key}",
                   source_filepath,
                   target_filepath)).communicate()
        except FileNotFoundError:
            raise FFmpegNotFoundError('mp4decrypt binary was not found at the specified path')

    def _get_license_key(self) -> str:
        try:
            return b64decode(
                self.http.post(
                    url=self.kinescope_video.get_clearkey_license_url(),
                    headers={'origin': KINESCOPE_BASE_URL},
                    json={
                        'kids': [
                            b64encode(bytes.fromhex(
                                self.mpd_master
                                .periods[0]
                                .adaptation_sets[0]
                                .content_protections[0]
                                .cenc_default_kid.replace('-', '')
                            )).decode().replace('=', '')
                        ],
                        'type': 'temporary'
                    }
                ).json()['keys'][0]['k'] + '=='
            ).hex() if self.mpd_master.periods[0].adaptation_sets[0].content_protections else None
        except KeyError:
            raise UnsupportedEncryption(
                "Unfortunately, only the ClearKey encryption type is currently supported, "
                "but not the one in this video"
            )

    def _fetch_segment(self,
                       segment_url: str,
                       file):
        for _ in range(5):
            try:
                copyfileobj(
                    BytesIO(self.http.get(segment_url, stream=True).content),
                    file
                )
                return
            except ChunkedEncodingError:
                pass

        raise SegmentDownloadError(f'Failed to download segment {segment_url}')

    def _fetch_segments(self,
                        segments_urls: list[str],
                        filepath: str | PathLike,
                        progress_bar_label: str = ''):
        visited_urls = set()
        with open(filepath, 'wb') as f:
            with tqdm(desc=progress_bar_label,
                      total=len(segments_urls),
                      bar_format='{desc}: {percentage:3.0f}%|{bar:10}| [{n_fmt}/{total_fmt}]') as progress_bar:
                for segment_url in segments_urls:
                    if segment_url not in visited_urls:
                        self._fetch_segment(segment_url, f)
                        visited_urls.add(segment_url)
                    progress_bar.update()

    def _get_segments_urls(self, resolution: tuple[int, int]) -> dict[str:list[str]]:
        try:
            result = {}
            for adaptation_set in self.mpd_master.periods[0].adaptation_sets:
                resolutions = [(r.width, r.height) for r in adaptation_set.representations]
                idx = resolutions.index(resolution) if adaptation_set.representations[0].height else 0
                representation = adaptation_set.representations[idx]
                base_url = representation.base_urls[0].base_url_value
                result[adaptation_set.mime_type] = [
                    base_url + (segment_url.media or '') 
                    for segment_url in representation.segment_lists[0].segment_urls if segment_url.media]

            return result
        except ValueError:
            raise InvalidResolution('Invalid resolution specified')

    def _fetch_mpd_master(self) -> MPEGDASH:
        return MPEGDASHParser.parse(self.http.get(
            url=self.kinescope_video.get_mpd_master_playlist_url(),
            headers={'Referer': KINESCOPE_BASE_URL}
        ).text)

    def get_resolutions(self) -> list[tuple[int, int]]:
        for adaptation_set in self.mpd_master.periods[0].adaptation_sets:
            if adaptation_set.representations[0].height:
                return [(r.width, r.height) for r in sorted(adaptation_set.representations, key=lambda r: r.height)]

    def download(self, filepath: str, resolution: tuple[int, int] = None):
        if not resolution:
            resolution = self.get_resolutions()[-1]

        key = self._get_license_key()
        audio_suffix, video_suffix, enc_suffix = '.aac', '.mp4', '.enc'
        tmp_base_path = self.temp_path / self.kinescope_video.video_id
        elements = [{
            'url': self._get_segments_urls(resolution)['audio/mp4'],
            'type': 'Audio',
            'suffix': audio_suffix,
            }, {
            'url': self._get_segments_urls(resolution)['video/mp4'],
            'type': 'Video',
            'suffix': video_suffix,
        }]
        if self.audio_only:
            elements.pop()

        for el in elements:
            tmp_path = Path(f'{tmp_base_path}_{el["type"]}{el["suffix"]}')
            el['tmp_path'] = tmp_path
            el['enc_path'] = tmp_path.with_suffix(tmp_path.suffix + enc_suffix)
            fetch_path = el['enc_path'] if key else tmp_path
            self._fetch_segments(el['url'], fetch_path, el['type'])

        if key:
            print('[*] Decrypting...', end=' ')
            for el in elements:
                self._decrypt_video(el['enc_path'], el['tmp_path'], key)
            print('Done')

        filepath = Path(filepath).with_suffix(elements[-1]['suffix'])
        filepath.parent.mkdir(parents=True, exist_ok=True)

        if not self.audio_only:
            print('[*] Merging tracks...', end=' ')
            self._merge_tracks([el['tmp_path'] for el in elements], filepath)
        else:
            move(elements[0]['tmp_path'], filepath)
        print('Done')
