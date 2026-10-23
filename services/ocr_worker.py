"""Isolated EasyOCR worker: model setup is explicit, never during a web request."""
import argparse
import json
import os
from pathlib import Path

MODEL_DIR = Path(os.environ.get('EASYOCR_MODEL_DIR', Path.home() / '.EasyOCR' / 'model'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true')
    parser.add_argument('image', nargs='?')
    args = parser.parse_args()
    import torch
    import easyocr
    torch.set_num_threads(2)
    reader = easyocr.Reader(['ru', 'en'], gpu=False, model_storage_directory=str(MODEL_DIR),
                            download_enabled=args.install, verbose=False, quantize=False)
    if args.install:
        print('Русская и английская модели установлены. Можно запускать сайт.')
        return
    if not args.image:
        parser.error('Укажите изображение или --install')
    from PIL import Image
    with Image.open(args.image) as picture:
        enlarge=max(picture.size)<1600
    results = reader.readtext(args.image, detail=1, paragraph=False, decoder='greedy',
                              batch_size=1, workers=0, canvas_size=2048,
                              mag_ratio=1.5 if enlarge else 1.0, text_threshold=0.6, low_text=0.3,
                              rotation_info=None)
    if not enlarge and sum(len(text.strip()) for _,text,score in results if score>.4)<30:
        # A sparse first pass may have missed small print. Retry at the larger scale.
        retry=reader.readtext(args.image,detail=1,paragraph=False,decoder='greedy',batch_size=1,
                              workers=0,canvas_size=2560,mag_ratio=1.5,rotation_info=None)
        if sum(len(text.strip()) for _,text,score in retry if score>.4)>sum(len(text.strip()) for _,text,score in results if score>.4):results=retry
    rows = []
    for box, text, confidence in results:
        rows.append({'text': text, 'confidence': float(confidence),
                     'x': float(min(p[0] for p in box)), 'y': float(min(p[1] for p in box)),
                     'w': float(max(p[0] for p in box)-min(p[0] for p in box)),
                     'h': float(max(p[1] for p in box)-min(p[1] for p in box))})
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == '__main__':
    main()
