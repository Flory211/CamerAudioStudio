"""Explicit one-time download of the official local inference models."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent / 'models'
FILES = {
    'selfie_segmenter_landscape.tflite': 'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter_landscape/float16/latest/selfie_segmenter_landscape.tflite',
    'face_landmarker.task': 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task',
    'vosk-model-small-ru-0.22.zip': 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip',
}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, url in FILES.items():
        path = ROOT / name
        if not path.is_file():
            temporary = path.with_suffix(path.suffix + '.download')
            print(f'Downloading {name}', flush=True)
            with urllib.request.urlopen(url, timeout=45) as source, temporary.open('wb') as output:
                size = 0
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > 128 * 1024 * 1024:
                        raise ValueError('Model exceeds expected size limit')
                    output.write(block)
            if temporary.stat().st_size < 1024:
                raise ValueError('Incomplete model download')
            temporary.replace(path)
        manifest[name] = dict(url=url, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist()) > 512 * 1024 * 1024:
                    raise ValueError('Model archive unexpectedly large')
                for info in archive.infolist():
                    parts = PurePosixPath(info.filename.replace('\\', '/')).parts
                    if not parts or '..' in parts or info.filename.startswith(('/', '\\')) or ':' in info.filename:
                        raise ValueError('Unsafe path in model archive')
                    target = ROOT.joinpath(*parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif not target.exists() or target.stat().st_size != info.file_size:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, target.open('wb') as output:
                            while block := source.read(1024 * 1024):
                                output.write(block)
        print(f'Ready: {name} ({path.stat().st_size} bytes)', flush=True)
    (ROOT / 'sources.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
