"""Relocation keeps approvals immutable; quiz checks target observed category placeholders."""
import hashlib
import json
from pathlib import Path

import pytest
from app.corpus_scope import validate_page_exclusion_manifest
from app.pdf_extractor import PDFExtractionResult
from app.study_materials import StudyMaterialManager


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fixture(tmp_path, old_path=None):
    root = tmp_path / 'project-name'
    src = root / 'src'; src.mkdir(parents=True)
    source = src / 'sample.txt'; source.write_text('A substantive source passage for physical page one.')
    image = root / 'data/evaluation/review/page.png'; image.parent.mkdir(parents=True)
    image.write_bytes(b'bound raster evidence')
    manifest = {'schema': 'approved-page-exclusions-v1', 'decision_id': 'fixture',
        'approved_by': 'user', 'approved_at': '2026-10-19', 'approval': 'EXCLUDE_EXACTLY_ONE',
        'approved_count': 1, 'pages': [{'filename': 'sample.txt', 'page': 1,
            'source_sha256': sha(source), 'image_path': old_path or str(tmp_path / 'old/location/project-name/data/evaluation/review/page.png'),
            'image_sha256': sha(image), 'reason': ['Clipped original: preserve exclusion.']}]}
    path = root / 'manifest.json'; path.write_text(json.dumps(manifest))
    return root, path, image, {'path': 'manifest.json', 'sha256': sha(path)}


def test_relocated_evidence_resolves_same_project_suffix_and_hash(tmp_path):
    root, path, image, entry = fixture(tmp_path)
    before = path.read_bytes()
    result = validate_page_exclusion_manifest(root, entry)
    assert result['pages'][0]['image_path'] == str(image)
    assert result['page_set'] == {('sample.txt', 1)}
    assert path.read_bytes() == before


def test_relative_project_root_keeps_absolute_evidence_result(tmp_path, monkeypatch):
    root, _, image, entry = fixture(tmp_path)
    monkeypatch.chdir(root.parent)
    result = validate_page_exclusion_manifest(Path(root.name), entry)
    assert Path(result['pages'][0]['image_path']).is_absolute()
    assert result['pages'][0]['image_path'] == str(image)


@pytest.mark.parametrize('old', ['/old/location/foreign-name/data/evaluation/review/page.png',
    '/old/location/project-name/data/evaluation/review/../review/page.png',
    '/old/location/project-name/src/page.png'])
def test_relocation_does_not_accept_arbitrary_external_or_traversal(tmp_path, old):
    root, _, _, entry = fixture(tmp_path, old)
    with pytest.raises(ValueError): validate_page_exclusion_manifest(root, entry)


def test_relocated_evidence_hash_mismatch_fails_closed(tmp_path):
    root, _, image, entry = fixture(tmp_path)
    image.write_bytes(b'tampered')
    with pytest.raises(ValueError): validate_page_exclusion_manifest(root, entry)


def test_relocated_evidence_symlink_fails_closed(tmp_path):
    root, _, image, entry = fixture(tmp_path)
    actual = root / 'actual.png'; actual.write_bytes(image.read_bytes())
    image.unlink(); image.symlink_to(actual)
    with pytest.raises(ValueError): validate_page_exclusion_manifest(root, entry)


class Responses:
    last_done_reason = 'stop'
    def __init__(self, questions): self.questions = questions; self.calls = []
    def check_health(self): return True
    def chat_complete(self, **kw):
        self.calls.append(kw)
        if 'flashcards' in kw['messages'][0]['content']:
            return '{"flashcards": []}'
        return json.dumps({'questions': self.questions})


def generate(answer, distractors, text):
    source = PDFExtractionResult(filename='synthetic.txt', filepath=Path('synthetic.txt'))
    source.extracted_pages = [{'page_num': 1, 'text': text}]
    source.total_chars = len(text); source.total_pages = 1
    client = Responses([{'format': 'source_cloze', 'answer': answer,
                         'distractors': distractors, 'evidence_id': 'Q1'}])
    result = StudyMaterialManager(client).generate_materials_from_pages(source, model='test-remote', quiz_count=1)
    return result, client


@pytest.mark.parametrize('distractors', [['unit', 'byte', 'dimension'], ['measurement', 'byte', 'quantity']])
def test_generic_categories_do_not_pass_as_unit_distractors(distractors):
    text = 'Entropy measures uncertainty in a random variable. Its entropy is one bit. A certain outcome has zero entropy.'
    result, client = generate('bit', distractors, text)
    assert result['generation']['quiz'] == 'extractive_fallback'
    assert len(client.calls) <= 3  # at most one bounded quiz repair plus flashcards


def test_concrete_alternatives_keep_full_source_context():
    text = 'In this source, a fair coin has two equally likely outcomes. Its entropy is one bit.'
    result, _ = generate('one bit', ['zero bits', 'two bits', 'three bits'], text)
    assert result['generation']['quiz'] == 'model'
    item = result['quiz'][0]
    assert item['question'].count('[…]') == 2  # source blank plus ending label
    assert text.replace('one bit', '[…]') in item['question']
    assert item['evidence_quote'] == text
    assert next(c['text'] for c in item['choices'] if c['label'] == item['answer']) == 'one bit'


@pytest.mark.parametrize('answer,distractors,text', [
    ('P(B)>0', ['P(A)>0', 'P(A|B)>0', 'P(B|A)>0'],
     'Bayes rule is P(A|B)=P(B|A)P(A)/P(B). It requires P(B)>0. The posterior concerns A given B.'),
    ('R>0', ['U>0', 'I>0', 'V>0'],
     'Điện trở có điều kiện R>0. Hiệu điện thế U và cường độ I liên hệ qua định luật Ôm U=IR.'),
])
def test_math_condition_choices_keep_the_same_quantity(answer, distractors, text):
    result, client = generate(answer, distractors, text)
    assert result['generation']['quiz'] == 'extractive_fallback'
    assert len(client.calls) <= 3


class ShortSpanRepairClient:
 last_done_reason='stop'
 def __init__(self):self.calls=[]
 def check_health(self):return True
 def chat_complete(self,**kw):
  self.calls.append(kw)
  if 'flashcards' in kw['messages'][0]['content']:return '{"flashcards":[]}'
  q={'format':'source_cloze','evidence_id':'Q1','answer':'R','distractors':['U','I','V']} if len(self.calls)==1 else {'format':'source_cloze','evidence_id':'Q1','answer':'R>0','distractors':['R<0','R=0','R<=0']}
  return json.dumps({'questions':[q]})
# Use three distractors false under R>0; R>=0 would also be true.
def test_short_repeated_math_variable_gets_one_repair():
 c=ShortSpanRepairClient();src=PDFExtractionResult(filename='s.txt',filepath=Path('s.txt'));text='Định luật Ôm là I = U/R, với R>0. Điện trở được đo bằng ôm.';src.extracted_pages=[{'page_num':1,'text':text}];src.total_chars=len(text);src.total_pages=1
 r=StudyMaterialManager(c).generate_materials_from_pages(src,model='test-remote',quiz_count=1);assert r['generation']['quiz']=='model';assert len(c.calls)==3
