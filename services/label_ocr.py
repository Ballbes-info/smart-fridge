"""Local Tesseract OCR and deliberately conservative label parsing. No remote API."""
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import warnings
from pathlib import Path
from datetime import date
from PIL import Image, ImageOps, UnidentifiedImageError

DATA = Path(__file__).resolve().parents[1] / 'resources' / 'ocr'
OCR_SLOTS = threading.BoundedSemaphore(2)
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


def recognize(raw):
    if not raw or len(raw) > 8*1024*1024: raise OCRError('Выберите изображение размером до 8 МБ.')
    if not OCR_SLOTS.acquire(blocking=False): raise OCRBusy('Распознавание занято. Повторите через несколько секунд.')
    try:
        return _recognize(raw)
    finally:
        OCR_SLOTS.release()


def _recognize(raw):
    command = executable()
    if not command or not available():
        raise OCRError('На сервере не установлен Tesseract. Пока доступен ручной ввод; инструкция установки — в README проекта.')
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
                image=ImageOps.autocontrast(ImageOps.grayscale(image))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise OCRError('Не удалось прочитать изображение. Выберите другое фото JPEG, PNG или WebP.') from exc
    try:
        with tempfile.TemporaryDirectory(prefix='smartfridge-ocr-') as tmp:
            path=Path(tmp)/'label.png'; image.save(path)
            result=subprocess.run([command,str(path),'stdout','--tessdata-dir',str(DATA),'-l','rus+eng','--psm','6'],
                                  capture_output=True,timeout=30,env={**os.environ,'OMP_THREAD_LIMIT':'1'})
            if result.returncode: raise OCRError('Распознать фото не удалось. Попробуйте другой ракурс или ручной ввод.')
            text=result.stdout.decode('utf-8',errors='replace').strip()[:30000]
            if not text: raise OCRError('Текст не найден. Снимите этикетку ближе, без бликов, или выделите другую область.')
            return text
    except subprocess.TimeoutExpired as exc: raise OCRError('Распознавание заняло слишком много времени. Попробуйте меньшую область.') from exc
    except OSError as exc: raise OCRError('Не удалось запустить распознавание. Проверьте установку Tesseract на сервере.') from exc


def parse_label(text):
    """Proposals, never authoritative product data. Bare dates are not assigned a role."""
    text=text[:30000]; proposals={}
    for field, pattern in [('protein',r'белк\w*'),('fat',r'жир\w*'),('carbs',r'углевод\w*')]:
        match=re.search(pattern+r'\s*[:—–-]?\s*(\d{1,3}(?:[.,]\d{1,3})?)\s*(?:г\b|g\b)?',text,re.I)
        if match: proposals[field]=match.group(1).replace(',','.')
    match=re.search(r'(\d{1,4}(?:[.,]\d+)?)\s*ккал',text,re.I)
    if match: proposals['calories']=match.group(1).replace(',','.')
    if re.search(r'на\s*100\s*(?:мл|ml)\b',text,re.I): proposals['nutrition_basis']='100ml'
    elif re.search(r'на\s*100\s*(?:г|g)\b',text,re.I): proposals['nutrition_basis']='100g'
    elif re.search(r'на\s*порци',text,re.I): proposals['nutrition_basis']='portion'
    for field, label in [('brand',r'(?:марка|бренд)'),('manufacturer',r'(?:изготовитель|производитель)')]:
        match=re.search(label+r'\s*[:—-]\s*([^\n]{2,160})',text,re.I)
        if match: proposals[field]=match.group(1).strip()
    match=re.search(r'состав\s*[:—-]?\s*(.+?)(?=\n\s*(?:пищевая|энергетическая|условия|хранить|срок|годен|изготовитель)|$)',text,re.I|re.S)
    if match: proposals['ingredients']=match.group(1).strip()[:3000]
    match=re.search(r'(?:масса\s*нетто|объ[её]м|содержимое)\s*[:—-]?\s*(\d+(?:[.,]\d+)?)\s*(кг|мл|г|л)\b',text,re.I)
    if match: proposals.update(quantity=match.group(1).replace(',','.'),unit={'кг':'kg','г':'g','мл':'ml','л':'l'}[match.group(2).lower()])
    date_pattern=r'(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})(?!\d)'
    found_dates=[]
    for match in re.finditer(date_pattern,text):
        try:
            day,month,year=map(int,match.groups());year=year+2000 if year<100 else year
            value=date(year,month,day).isoformat(); prefix=text[max(0,match.start()-55):match.start()].split('\n')[-1].lower()
            found_dates.append(value)
            if re.search(r'годен\s*до|употребить\s*до|срок\s*годности\s*до',prefix): proposals['expiry_date']=value
            elif re.search(r'изготовлен|дата\s*производства|дата\s*изготовления',prefix): proposals['manufactured_date']=value
        except ValueError: pass
    match=re.search(r'срок\s*годности\s*[:—-]?\s*(\d{1,4})\s*(сут\w*|дн\w*|месяц\w*|мес\.?|лет|год\w*)',text,re.I)
    if match: proposals.update(shelf_value=match.group(1),shelf_unit='months' if match.group(2).lower().startswith('мес') else 'years' if match.group(2).lower().startswith(('лет','год')) else 'days')
    # Name is only a candidate from the first short non-technical line, for explicit review.
    for line in text.splitlines()[:4]:
        line=line.strip()
        if 3<=len(line)<=120 and re.search('[а-яА-Яa-zA-Z]',line) and not re.search(r'состав|ккал|белк|жир|углевод|изготов|годен|масса|100\s*(?:г|мл)',line,re.I):
            proposals['name']=line; break
    return {'proposals':proposals,'dates':list(dict.fromkeys(found_dates)),'text':text}
