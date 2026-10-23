"""Free server-side label OCR. Photos never leave the SmartFridge server."""
import io
import importlib.util
import json
import sys
import os
import shutil
import subprocess
import tempfile
import threading
import warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from services.label_extraction import extract, parse_label, validate_nutrition

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


def easy_available():
    models = Path(os.environ.get('EASYOCR_MODEL_DIR', Path.home() / '.EasyOCR' / 'model'))
    installed = importlib.util.find_spec('easyocr') is not None
    return installed and all((models / name).is_file() for name in ['craft_mlt_25k.pth', 'cyrillic_g2.pth'])


def available():
    return easy_available() or bool(executable() and all((DATA / (lang+'.traineddata')).is_file() for lang in ['rus','eng']))


def recognize(raw, *, structured=False):
    if not raw or len(raw) > 8*1024*1024: raise OCRError('Выберите изображение размером до 8 МБ.')
    if not OCR_SLOTS.acquire(blocking=False): raise OCRBusy('Распознавание занято. Повторите через несколько секунд.')
    try:
        return _recognize(raw, structured=structured)
    finally:
        OCR_SLOTS.release()


def _recognize(raw, *, structured=False):
    command = executable()
    use_easy = easy_available()
    if not use_easy and (not command or not available()):
        raise OCRError('На сервере не установлен сканер этикеток. Установите EasyOCR по инструкции в README. Ручной ввод доступен.')
    image = prepare_image(raw)
    try:
        with tempfile.TemporaryDirectory(prefix='smartfridge-ocr-') as tmp:
            path=Path(tmp)/'label.png'; image.save(path)
            if use_easy:
                worker = Path(__file__).with_name('ocr_worker.py')
                result = subprocess.run([sys.executable, str(worker), str(path)], capture_output=True,
                                        timeout=80, env={**os.environ, 'OMP_NUM_THREADS':'2'})
                if result.returncode:
                    raise OCRError('Не удалось запустить сканер. Проверьте установку моделей: python -m services.ocr_worker --install')
                try:
                    rows = json.loads(result.stdout.decode('utf-8'))
                    if not isinstance(rows,list): raise ValueError('Expected text regions')
                except (ValueError, UnicodeError) as exc:
                    raise OCRError('Сканер вернул неполный ответ. Повторите снимок.') from exc
                parsed = extract(rows)
                parsed['engine'] = 'easyocr'
                # Independent second reading can confirm digits in a low-confidence phrase.
                # It never substitutes a conflicting value or guesses missing decimal points.
                if command and all((DATA/(lang+'.traineddata')).is_file() for lang in ['rus','eng']):
                    try:
                        verification=subprocess.run([command,str(path),'stdout','--tessdata-dir',str(DATA),
                                                     '-l','rus+eng','--psm','3'],capture_output=True,timeout=8,
                                                    env={**os.environ,'OMP_THREAD_LIMIT':'1'})
                        if verification.returncode==0:
                            other=parse_label(verification.stdout.decode('utf-8',errors='replace'))
                            for key,value in parsed['proposals'].items():
                                if other['proposals'].get(key)==value:
                                    parsed['confidence'][key]=max(.95,parsed['confidence'].get(key,0))
                            validate_nutrition(parsed)
                    except (subprocess.TimeoutExpired,OSError):
                        pass  # The primary result remains usable when the optional check fails.

            else:
                # Automatic page segmentation keeps columns separate, unlike a single forced block.
                image=ImageOps.autocontrast(ImageOps.grayscale(image)); image.save(path)
                result=subprocess.run([command,str(path),'stdout','--tessdata-dir',str(DATA),'-l','rus+eng','--psm','3'],
                                      capture_output=True,timeout=30,env={**os.environ,'OMP_THREAD_LIMIT':'1'})
                if result.returncode: raise OCRError('Распознать фото не удалось. Попробуйте другой ракурс.')
                parsed=parse_label(result.stdout.decode('utf-8',errors='replace').strip()[:30000])
                parsed['engine']='tesseract'
            if not parsed['text'].strip():
                raise OCRError('Текст не найден. Снимите этикетку ближе, без бликов.')
            return parsed if structured else parsed['text']
    except subprocess.TimeoutExpired as exc:
        raise OCRError('Сканирование заняло слишком много времени. Снимите этикетку ближе или выделите область с текстом.') from exc
    except OSError as exc:
        raise OCRError('Не удалось запустить сканер. Проверьте установку движка на сервере.') from exc


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
