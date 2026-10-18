"""OCR text is source content, never an assistant's surrounding commentary."""
import io
import json
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

from app.vision_ocr import VisionOCRManager


def image_bytes(blank=False):
    image = Image.new('RGB', (200, 100), 'white')
    if not blank:
        ImageDraw.Draw(image).text((10, 10), 'Entropy', fill='black')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def test_uniform_image_has_no_transcript_or_model_call(monkeypatch):
    post = Mock(side_effect=AssertionError('Blank image must not generate content'))
    monkeypatch.setattr('app.vision_ocr.requests.post', post)
    assert VisionOCRManager()._ocr_ollama_vlm(image_bytes(blank=True)) == ''
    post.assert_not_called()


def test_vision_ocr_extracts_only_explicit_transcript(monkeypatch):
    text = 'Entropy measures uncertainty.\nx = a / b'
    response = Mock(status_code=200)
    response.json.return_value = {'response': json.dumps({'text': text}), 'done_reason': 'stop'}
    post = Mock(return_value=response)
    monkeypatch.setattr('app.vision_ocr.requests.post', post)
    assert VisionOCRManager()._ocr_ollama_vlm(image_bytes()) == text
    assert post.call_args.kwargs['json']['format']['required'] == ['text']


@pytest.mark.parametrize('raw', [
    'Here is the transcript: Entropy measures uncertainty.',
    '{"text": 123}', '{"text": "Entropy", "commentary": "Guess"}',
])
def test_vision_ocr_rejects_non_transcript_payloads(monkeypatch, raw):
    response = Mock(status_code=200)
    response.json.return_value = {'response': raw, 'done_reason': 'stop'}
    monkeypatch.setattr('app.vision_ocr.requests.post', Mock(return_value=response))
    with pytest.raises(RuntimeError):
        VisionOCRManager()._ocr_ollama_vlm(image_bytes())


def test_vision_ocr_rejects_truncated_structured_output(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {'response': '{"text": "Entropy"}', 'done_reason': 'length'}
    monkeypatch.setattr('app.vision_ocr.requests.post', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='length'):
        VisionOCRManager()._ocr_ollama_vlm(image_bytes())
