"""Free server-side label OCR. Photos never leave the SmartFridge server."""
import io
import csv
import sys
import os
import shutil
import subprocess
import tempfile
import threading
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from services.label_extraction import extract, parse_label

DATA = Path(__file__).resolve().parents[1] / 'resources' / 'ocr'
OCR_SLOTS = threading.BoundedSemaphore(1)
Image.MAX_IMAGE_PIXELS = 20_000_000


class OCRError(Exception): pass
class OCRBusy(OCRError): pass


def executable():
    configured = os.environ.get('TESSERACT_CMD', '').strip()
    candidates = [configured, shutil.which('tesseract')]
    for root in [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')]:
        if root: candidates.append(str(Path(root) / 'Tesseract-OCR' / 'tesseract.exe'))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def available():
    return bool(executable() and all((DATA / (lang+'.traineddata')).is_file() for lang in ['rus','eng']))


def recognize(raw, *, structured=False):
    if not raw or len(raw) > 8*1024*1024: raise OCRError('Выберите изображение размером до 8 МБ.')
    if not OCR_SLOTS.acquire(blocking=False): raise OCRBusy('Распознавание занято. Повторите через несколько секунд.')
    try:
        return _recognize(raw, structured=structured)
    finally:
        OCR_SLOTS.release()


def _recognize(raw, *, structured=False):
    command = executable()
    if not available():
        raise OCRError('На сервере не установлен Tesseract. Ручной ввод и GigaChat доступны отдельно.')
    image = ImageOps.autocontrast(ImageOps.grayscale(prepare_image(raw)))
    try:
        with tempfile.TemporaryDirectory(prefix='smartfridge-ocr-') as tmp:
            path=Path(tmp)/'label.png'; image.save(path)
            result=subprocess.run([command,str(path),'stdout','--tessdata-dir',str(DATA),
                                   '-l','rus+eng','--psm','3','-c','tessedit_create_tsv=1'],capture_output=True,timeout=30,
                                  env={**os.environ,'OMP_THREAD_LIMIT':'1'})
            if result.returncode: raise OCRError('Распознать фото не удалось. Попробуйте другой ракурс.')
            rows=[]
            for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8',errors='replace')),delimiter='\t'):
                try:
                    if row.get('text','').strip() and float(row['conf'])>=0:
                        rows.append(dict(text=row['text'],confidence=float(row['conf'])/100,
                                         x=float(row['left']),y=float(row['top']),w=float(row['width']),h=float(row['height'])))
                except (ValueError,KeyError,TypeError):continue
            parsed=extract(rows)
            parsed['engine']='tesseract'
            if not parsed['text'].strip():
                raise OCRError('Текст не найден. Снимите этикетку ближе, без бликов.')
            return parsed if structured else parsed['text']
    except subprocess.TimeoutExpired as exc:
        raise OCRError('Сканирование заняло слишком много времени. Выделите область с текстом.') from exc
    except OSError as exc:
        raise OCRError('Не удалось запустить Tesseract. Проверьте установку на сервере.') from exc


def prepare_image(raw):
    if not raw or len(raw) > 8*1024*1024: raise OCRError('Выберите изображение размером до 8 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if original.format not in ['JPEG','PNG','WEBP']: raise OCRError('Поддерживаются JPEG, PNG и WebP.')
                if original.width*original.height > 20_000_000: raise OCRError('Изображение слишком большое: максимум 20 мегапикселей.')
                original.draft('RGB',(2400,2400))
                oriented=ImageOps.exif_transpose(original);oriented.thumbnail((2400,2400))
                rgba=oriented.convert('RGBA')
                image=Image.new('RGB',rgba.size,'white');image.paste(rgba,mask=rgba.getchannel('A'))
                image.thumbnail((2400,2400))
                if min(image.size) < 30: raise OCRError('Выделенная область слишком мала для распознавания.')

    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise OCRError('Не удалось прочитать изображение. Выберите другое фото JPEG, PNG или WebP.') from exc
    return image


def runtime_status():
    return {'engine':'tesseract' if available() else 'unavailable',
            'python':f'{sys.version_info.major}.{sys.version_info.minor}'}
