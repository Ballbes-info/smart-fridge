# Tesseract language data

`eng.traineddata` and `rus.traineddata` are official `tesseract-ocr/tessdata_fast` LSTM language files for Tesseract 4/5, licensed under Apache-2.0; see LICENSE.

Sources (downloaded 2026-10-23):
- https://github.com/tesseract-ocr/tessdata_fast/blob/main/eng.traineddata
- https://github.com/tesseract-ocr/tessdata_fast/blob/main/rus.traineddata

The application passes this directory explicitly as `--tessdata-dir`. It does not download language models at runtime. Tesseract itself is a system dependency; installation is described in the project README.
