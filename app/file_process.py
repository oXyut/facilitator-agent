import os
import uuid
from tempfile import NamedTemporaryFile

from fastapi import UploadFile
from google.cloud import storage
from pydub import AudioSegment


class GcsFile:
    def __init__(self, bucket_name: str, blob_name: str):
        self.bucket_name = bucket_name
        self.blob_name = blob_name
        self.gcs_path = f"gs://{bucket_name}/{blob_name}"


def upload_file(file_path: str, bucket_name: str, blob_name: str) -> None:
    """
    Upload a file to Google Cloud Storage bucket.

    Args:
        file_path (str): Local path to the file to upload
        bucket_name (str): Name of the GCS bucket
        blob_name (str): Name to give the file in GCS (path/to/file)

    Returns:
        None
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(file_path)


def delete_gcs_file(gcs_file: GcsFile) -> None:
    storage_client = storage.Client()
    bucket = storage_client.bucket(gcs_file.bucket_name)
    blob = bucket.blob(gcs_file.blob_name)
    blob.delete()


def convert_webm_to_mp3(webm_path: str) -> str:
    """
    WebMファイルをMP3形式に変換する

    Args:
        webm_path (str): 変換元のWebMファイルのパス

    Returns:
        str: 変換後のMP3ファイルのパス
    """
    # 出力ファイルパスの生成（拡張子をmp3に変更）
    mp3_path = os.path.splitext(webm_path)[0] + ".mp3"

    # WebMファイルを読み込み
    audio = AudioSegment.from_file(webm_path, format="webm")

    # MP3として出力（ビットレート192kbps）
    audio.export(mp3_path, format="mp3", bitrate="192k")

    return mp3_path


def mix_audio_files(webm_1_path: str, webm_2_path: str, position: int = 0) -> str:
    AudioSegment.converter = "/usr/bin/ffmpeg"
    audio_1: AudioSegment = AudioSegment.from_file(webm_1_path, format="webm")
    audio_2: AudioSegment = AudioSegment.from_file(webm_2_path, format="webm")
    mixed_audio = audio_1.overlay(audio_2, position=position)
    mixed_audio.export("mixed_audio.mp3", format="mp3")
    return "mixed_audio.mp3"


async def process_webm_file(
    host_audio: UploadFile, meet_audio: UploadFile, bucket_name: str
) -> GcsFile:
    # temp file
    with NamedTemporaryFile(delete=False, suffix=".webm") as host_temp_webm:
        content = await host_audio.read()
        host_temp_webm.write(content)
        host_temp_webm_path = host_temp_webm.name

    with NamedTemporaryFile(delete=False, suffix=".webm") as meet_temp_webm:
        content = await meet_audio.read()
        meet_temp_webm.write(content)
        meet_temp_webm_path = meet_temp_webm.name

    # mix audio files
    mixed_audio_path = mix_audio_files(host_temp_webm_path, meet_temp_webm_path)

    # upload to gcs
    mixed_audio_blob_name = f"audio/{str(uuid.uuid4())}.mp3"
    upload_file(mixed_audio_path, bucket_name, mixed_audio_blob_name)

    # delete temp files
    os.unlink(host_temp_webm_path)
    os.unlink(meet_temp_webm_path)
    os.unlink(mixed_audio_path)

    return GcsFile(bucket_name, mixed_audio_blob_name)
