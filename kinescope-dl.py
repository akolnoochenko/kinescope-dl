#!/usr/bin/env python3
import os
import click
from urllib.parse import urlparse

from kinescope import KinescopeVideo, KinescopeDownloader


class URLType(click.ParamType):
    name = 'url'

    def convert(self, value, param, ctx):
        try:
            parsed_url = urlparse(value)
            if parsed_url.scheme and parsed_url.netloc:
                return value
            else:
                self.fail(f'Expected valid url. Got {value}', param, ctx)
        except Exception as E:
            self.fail(f'Expected valid url. Got {value}: {E}', param, ctx)


@click.command()
@click.option(
    '--referer', '-r',
    required=False, help='Referer url of the site where the video is embedded', type=URLType()
)
@click.option(
    '--best-quality',
    default=False, required=False, help='Automatically select the best possible quality', is_flag=True
)
@click.option(
    '--audio-only',
    default=False, required=False, help='Only audio download', is_flag=True
)
@click.option(
    '--temp',
    default='./temp', required=False, help='Path to directory for temporary files', type=click.Path()
)
@click.argument('input_url', type=URLType())
@click.argument('output_file', type=click.Path())
@click.option("--ffmpeg-path", default='./ffmpeg', required=False, help='Path to ffmpeg executable', type=click.Path())
@click.option("--mp4decrypt-path", default='./mp4decrypt', required=False, help='Path to mp4decrypt executable', type=click.Path())
def main(referer,
         best_quality, audio_only,
         temp, 
         input_url,
         output_file,
         ffmpeg_path,
         mp4decrypt_path):
    """
    Kinescope-dl: Video downloader for Kinescope

    \b
    <INPUT_URL> is url of the Kinescope video
    <OUTPUT_FILE> is path to the output file
    """

    kinescope_video: KinescopeVideo = KinescopeVideo(
        url=input_url,
        referer_url=referer
    )

    downloader: KinescopeDownloader = KinescopeDownloader(
            kinescope_video, temp,
            ffmpeg_path=os.environ.get('FFMPEG_PATH', './ffmpeg'),
            mp4decrypt_path=os.environ.get('MP4DECRYPT_PATH', './mp4decrypt'),
            audio_only=audio_only)

    print('= OPTIONS ============================')
    video_resolutions = downloader.get_resolutions()
    if audio_only or best_quality:
        res_index = -1
    else:
        res_index = int(input(
            '   '.join([f'{i + 1}) {r[1]}p' for i, r in enumerate(video_resolutions)]) +
            '\n> Quality: ')) - 1
    chosen_resolution = video_resolutions[res_index]
    print(f'[*] {chosen_resolution[1]}p is selected')
    print('======================================')

    print('\n= DOWNLOADING =================')
    downloader.download(
        output_file if output_file else f'{kinescope_video.video_id}.mp4',
        chosen_resolution
    )
    print('===============================')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[*] Interrupted')
