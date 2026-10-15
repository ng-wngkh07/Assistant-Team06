"""Independent source-review regression fixtures; contains no private source data."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from app.source_page_reviews import validate_page_review, reviewed_page_text


@pytest.fixture
def valid_review(tmp_path):
    image = tmp_path / 'page.png'
    image.write_bytes(b'synthetic image evidence')
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    raw = tmp_path / 'raw.json'
    raw.write_text(json.dumps(dict(source_relpath='synthetic.pdf', page_num=1,
                                  source_hash='synthetic-source-sha', image_hash=image_hash,
                                  raw_text='native\u000cfont capture')))
    return dict(filename='synthetic.pdf', page=1, source_sha256='synthetic-source-sha',
                kind='printed', completeness='complete', image_reviewed=True,
                image_path=str(image), image_sha256=image_hash,
                raw_capture_path=str(raw), raw_capture_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                transcript='Verified source.', editorial='', unresolved=[])


def validate(review):
    validate_page_review(review, 'synthetic.pdf', 1, 'synthetic-source-sha')


@pytest.mark.parametrize('field', ['transcript', 'visual_description', 'editorial', 'unresolved'])
@pytest.mark.parametrize('control', ['\x00', '\x07', '\x08', '\x0b', '\x0c', '\x1f'])
def test_review_rejects_control_corruption_in_each_text_field(valid_review, field, control):
    review = copy.deepcopy(valid_review)
    review['completeness'] = 'partial'
    value = 'source ' + control + ' symbol'
    review[field] = [value] if field == 'unresolved' else value
    with pytest.raises(ValueError):
        validate(review)


def test_literal_latex_and_code_whitespace_survive_validation(valid_review):
    literal = r'\frac{1}{2} \binom{n}{k} \alpha \varepsilon \to \text{a} \right) \ne 0'
    code = 'if (ready) {\r\n\treturn "\\n";\r\n}\n'
    valid_review['transcript'] = literal + '\n' + code
    before = copy.deepcopy(valid_review)
    validate(valid_review)
    assert valid_review == before
    assert literal in reviewed_page_text(valid_review)
    assert code in reviewed_page_text(valid_review)
    assert '\x0c' in json.loads(Path(valid_review['raw_capture_path']).read_text())['raw_text']


@pytest.mark.parametrize('field', ['transcript', 'visual_description', 'editorial', 'unresolved'])
def test_legitimate_multiline_fields_are_preserved(valid_review, field):
    valid_review['completeness'] = 'partial'
    value = 'line one\n\tline two\r\n'
    valid_review[field] = [value] if field == 'unresolved' else value
    before = copy.deepcopy(valid_review)
    validate(valid_review)
    assert valid_review == before
